"""Configured layout. Source extraction and scientific interpretation stay outside.

Layout notes
------------
Every figure/table/equation block is kept atomic (figure + translated caption +
figure-label notes + any attached caption panels).  Two mechanisms reduce the
large bottom gaps that a too-tall atomic block would otherwise leave behind:

* ``mode='right'``  - figure left, caption column right, for blocks whose
  caption column still leaves enough width on the page; the figure may use a
  smaller scale from ``layout.figure_side_scales`` when that makes it fit.
* reflow passes     - after a build the module measures each page's bottom
  whitespace; blocks that did not fit are then emitted *after* the following
  text (never past the next figure-family block, never before their own
  in-text position) and the document is rebuilt.  The best pass wins.

Placement decisions are logged in the build receipt so reviews stay auditable.
"""
from pathlib import Path
import hashlib
import json

from PIL import Image as PILImage
from reportlab.platypus import (BaseDocTemplate, Frame, PageTemplate, Paragraph,
    Image, Spacer, KeepTogether, NextPageTemplate, PageBreak, Table, TableStyle)
from reportlab.lib.styles import ParagraphStyle

from .config import digest, write_json
from .typography import Typography, chinese_wrapping, CLOSE, OPEN

FIG_TYPES = {'figure', 'table', 'equation'}
SIDE_GAP_PT = 12.0
_PIXEL_CACHE = {}


def pixel_hash(image):
    im = image.convert('RGB')
    return hashlib.sha256(str(im.size).encode()+im.tobytes()).hexdigest()


def crop(paper):
    import pypdfium2 as pdfium
    source = paper.path(paper.config['source_pdf'])
    source_hash = digest(source)
    cache_path = paper.work/'crop_cache.json'
    cache = json.loads(cache_path.read_text(encoding='utf8')) if cache_path.exists() else {}
    result = []
    with pdfium.PdfDocument(str(source)) as doc:
        for asset in paper.assets.values():
            path = paper.asset_path(asset)
            key = hashlib.sha256(json.dumps([source_hash, asset['page'], asset['bbox'],
                                             asset.get('dpi', 600), 'draw_annots=True']).encode()).hexdigest()
            if path.exists() and cache.get(asset['id'], {}).get('key') == key and cache[asset['id']].get('sha256') == digest(path):
                result.append({'id': asset['id'], 'cached': True}); continue
            if path.exists():
                raise FileExistsError(f'Changed/untracked crop already exists: {path}; choose a new path')
            if asset['page'] > len(doc):
                raise ValueError('Crop page exceeds source PDF')
            page = doc[asset['page']-1]; w, h = page.get_size()
            x0, y0, x1, y1 = asset['bbox']
            if x1 > w or y1 > h:
                raise ValueError(f"Crop outside displayed page: {asset['id']}")
            dpi = asset.get('dpi', 600)
            if dpi < 72:
                raise ValueError('Crop DPI must be at least 72')
            image = page.render(scale=dpi/72, crop=(x0,h-y1,w-x1,y0), draw_annots=True).to_pil()
            path.parent.mkdir(parents=True, exist_ok=True); image.save(path, format='PNG')
            cache[asset['id']] = {'key': key, 'sha256': digest(path), 'pixels': list(image.size)}
            result.append({'id': asset['id'], 'cached': False})
    write_json(cache_path, cache)
    return result


def styles(paper, fonts):
    size = paper.layout['body_size_pt']; leading = paper.layout['leading_pt']
    base = dict(fontName=fonts['song'], fontSize=size, leading=leading, spaceAfter=7,
                wordWrap='CJK', allowWidows=0, allowOrphans=0)
    result = {}
    for name, changes in {
        'body': {'firstLineIndent': size*2},
        'abstract': {'fontName': fonts['kai'], 'fontSize': 7.5, 'leading': 11.5},
        'small': {'fontName': fonts['kai'], 'fontSize': 7.5, 'leading': 11.5, 'spaceAfter': 3},
        'caption': {'fontName': fonts['kai'], 'fontSize': 7.5, 'leading': 11.5, 'spaceAfter': 5},
        'heading': {'fontSize': 13, 'leading': 20, 'spaceBefore': 12, 'spaceAfter': 9, 'keepWithNext': True},
        'title_en': {'fontName': fonts['bold'], 'fontSize': 15, 'leading': 18, 'keepWithNext': True},
        'title_cn': {'fontSize': 18, 'leading': 24, 'keepWithNext': True},
        'meta': {'fontSize': 9, 'leading': 13, 'spaceAfter': 3},
        'references': {'fontName': fonts['roman'], 'fontSize': 9, 'leading': 12,
                       'spaceAfter': 5, 'leftIndent': 12, 'firstLineIndent': -12}
    }.items():
        result[name] = ParagraphStyle(name, **(base | changes))
    return result


class TrackedParagraph(Paragraph):
    def draw(self):
        if self.style.name in {'title_en', 'title_cn', 'heading'}:
            # SimSun has no bundled bold face. Fill + stroke preserves its
            # glyphs and searchable text while making every heading bold.
            self.canv.saveState()
            try:
                self.canv.setStrokeColor(self.style.textColor)
                self.canv.setLineWidth(self.style.fontSize * 0.018)
                self.canv._code.append('BT 2 Tr ET')
                super().draw()
            finally:
                self.canv.restoreState()
        else:
            super().draw()
        # Split fragments keep the same class. Content-derived IDs are logged
        # separately in the receipt; line checks also cover split fragments.
        for line in self.blPara.lines:
            if hasattr(line, 'words'):
                text = ''.join(x.text for x in line.words).strip()
            else:
                text = ' '.join(line[1]).strip()
            if not text:
                continue
            if text[0] in CLOSE or text[-1] in OPEN:
                self.canv._paper_log['line_issues'].append({'page': self.canv.getPageNumber(), 'text': text})


class TrackedImage(Image):
    def __init__(self, filename, asset, scale=None):
        chosen = float(asset.get('scale', 1.0)) if scale is None else float(scale)
        if not 0.8 <= chosen <= 1.2:
            raise ValueError(f"Asset scale must be between 0.8 and 1.2: {asset['id']}")
        super().__init__(str(filename), width=asset['width_pt']*chosen, height=asset['height_pt']*chosen)
        self.asset = asset
        self.scale = chosen
        key = str(filename)
        if key not in _PIXEL_CACHE:
            with PILImage.open(filename) as im:
                _PIXEL_CACHE[key] = pixel_hash(im)
        self.pixel_sha = _PIXEL_CACHE[key]

    def draw(self):
        super().draw()
        x, y = self.canv.absolutePosition(0, 0)
        self.canv._paper_log['images'].append({'id': self.asset['id'], 'page': self.canv.getPageNumber(),
            'x': x, 'y': y, 'width': self.drawWidth, 'height': self.drawHeight,
            'scale': round(self.scale, 4), 'pixel_sha256': self.pixel_sha})


def figure_group(paper, block, paragraph, mode='below', scale=None):
    """Flowables for one atomic figure block, the image, and the mode used."""
    asset = paper.assets[block['asset']]
    image = TrackedImage(paper.asset_path(asset), asset, scale=scale)
    text_parts = []
    if block['type'] != 'equation':
        text_parts.append(paragraph(block['caption'], 'caption', block['id']+':caption'))
        notes = block.get('notes')
        if notes:
            label = '表内文字对应：' if block['type'] == 'table' else '图中说明：'
            text_parts.append(paragraph(label+notes, 'caption', block['id']+':notes'))
    for attached in paper.attachments.get(block['id'], []):
        text_parts.append(Spacer(1, 6))
        text_parts.append(TrackedImage(paper.asset_path(attached), attached))
    inner = paper.layout['width_pt'] - 2*paper.layout['margin_pt']
    if mode == 'right' and text_parts:
        column = inner - image.drawWidth - SIDE_GAP_PT
        if column >= side_min_column(paper, block):
            table = Table([[image, text_parts]],
                          colWidths=[image.drawWidth+SIDE_GAP_PT, column],
                          style=TableStyle([
                              ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                              ('LEFTPADDING', (0, 0), (-1, -1), 0),
                              ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                              ('TOPPADDING', (0, 0), (-1, -1), 0),
                              ('BOTTOMPADDING', (0, 0), (-1, -1), 0)]))
            return [table, Spacer(1, 7)], image, 'right'
    return [image, Spacer(1, 7), *text_parts, Spacer(1, 7)], image, 'below'


def group_height(paper, parts):
    inner = paper.layout['width_pt'] - 2*paper.layout['margin_pt']
    with chinese_wrapping():
        return sum(p.wrap(inner, 100000)[1]+p.getSpaceBefore()+p.getSpaceAfter() for p in parts)


def side_geometry(paper, block, paragraph, scale):
    """(height, column width) if the caption could sit right of the figure."""
    asset = paper.assets[block['asset']]
    inner = paper.layout['width_pt'] - 2*paper.layout['margin_pt']
    image_w = asset['width_pt']*scale
    column = inner - image_w - SIDE_GAP_PT
    if column < side_min_column(paper, block):
        return None, column
    parts, _, _ = figure_group(paper, block, paragraph, mode='right', scale=scale)
    return group_height(paper, parts), column


def side_min_column(paper, block):
    """Right column must hold the caption text and every attached panel."""
    attached = [a['width_pt'] for a in paper.attachments.get(block['id'], [])]
    return max(paper.layout['figure_side_min_col_pt'], max(attached, default=0.0)+1)


def measure_blanks(pdf, margin):
    """Bottom whitespace per page, page-number artifact excluded by position."""
    import pdfplumber
    result = {}
    with pdfplumber.open(str(pdf)) as doc:
        for number, page in enumerate(doc.pages, 1):
            height = page.height
            chars = [c for c in page.chars if c['top'] < height-46]
            bottom = max([c['bottom'] for c in chars]+[i['bottom'] for i in page.images], default=0.0)
            result[number] = {'height': height, 'blank': max(0.0, (height-margin)-bottom)}
    return result


def max_blank(blanks):
    if not blanks:
        return 0.0
    last = max(blanks)
    return max((v['blank'] for k, v in blanks.items() if k != last), default=0.0)


def blank_score(paper, blanks):
    """Worst-page-first score: a single huge hole must not be traded for many small ones."""
    if not blanks:
        return 0.0
    target = paper.layout['gap_target_pt']
    last = max(blanks)
    excess = sum(max(0.0, v['blank']-target) for k, v in blanks.items() if k != last)
    worst = max((v['blank'] for k, v in blanks.items() if k != last), default=0.0)
    return worst*3.0 + excess


def _order_with_hops(paper, hops):
    """Emission order for one hop map (block id -> anchors it drifts past)."""
    blocks = paper.blocks
    anchors = {}
    for index, block in enumerate(blocks):
        if block['id'] not in hops:
            continue
        remaining = int(hops[block['id']])
        if remaining <= 0:
            continue
        for later in blocks[index+1:]:
            if (later['type'] in FIG_TYPES and not later.get('attach_to')
                    and later['id'] not in hops):
                remaining -= 1
                if remaining <= 0:
                    anchors[block['id']] = later['id']
                    break
    out = []; pending = {}
    for block in blocks:
        bid = block['id']
        is_anchor = block['type'] in FIG_TYPES and not block.get('attach_to')
        if bid in anchors:
            pending.setdefault(anchors[bid], []).append(bid)
            continue
        if is_anchor and bid in pending:
            out.extend(pending.pop(bid))
        out.append(bid)
    for rest in pending.values():
        out.extend(rest)
    return out


def _figure_sequence(paper, order):
    by_id = {b['id']: b for b in paper.blocks}
    return [bid for bid in order
            if by_id[bid]['type'] in FIG_TYPES and not by_id[bid].get('attach_to')]


def _order_violations(paper, order, hops):
    """Deferred blocks emitted before a figure they must not overtake (STA-009)."""
    rank = {b['id']: i for i, b in enumerate(paper.blocks)}
    sequence = _figure_sequence(paper, order)
    offenders = []
    for earlier in range(len(sequence)):
        for later in range(earlier+1, len(sequence)):
            if rank[sequence[earlier]] > rank[sequence[later]]:
                for bid in (sequence[earlier], sequence[later]):
                    if hops.get(bid, 0) > 0 and bid not in offenders:
                        offenders.append(bid)
    return offenders


def emission_order(paper, deferred):
    """Move deferred blocks just before the anchor figure they were queued for.

    ``deferred`` maps block id -> hops (how many following non-deferred
    figure-family blocks the block drifts past); a plain list means one hop.
    A block with no such anchor keeps its original place, so nothing can drift
    to the end of the document.  Figure numbers must stay in source order on
    paper, so a deferral that would overtake a figure staying in place has its
    hops reduced, and finally falls back to staying put (STA-009).
    """
    hops = dict(deferred) if isinstance(deferred, dict) else {bid: 1 for bid in deferred}
    order = _order_with_hops(paper, hops)
    for _ in range(len(hops)+2):
        offenders = _order_violations(paper, order, hops)
        if not offenders:
            return order
        for bid in offenders:
            hops[bid] = 1 if hops.get(bid, 0) > 1 else 0
        order = _order_with_hops(paper, hops)
    return _order_with_hops(paper, {bid: 0 for bid in hops})


def plan_reflow(paper, state, blanks, receipt, paragraph, by_id, order):
    """Next (variants, deferred) plan, or None when nothing can improve."""
    page_of = {}
    image_page = {im['id']: im['page'] for im in receipt['layout']['images']}
    for block in paper.blocks:
        if block['type'] in FIG_TYPES and not block.get('attach_to'):
            page_of[block['id']] = image_page.get(block['asset'])
    gaps = [(k, v['blank']) for k, v in sorted(blanks.items())
            if k != max(blanks) and v['blank'] > paper.layout['gap_target_pt']]
    index = {b['id']: i for i, b in enumerate(paper.blocks)}
    variants = json.loads(json.dumps(state['variants']))
    deferred = dict(state['deferred'])
    max_hops = int(paper.layout.get('max_defer_hops', 2))
    changed = False
    for page, blank in sorted(gaps, key=lambda item: -item[1]):
        candidate = None
        for bid in order:
            block = by_id[bid]
            if block['type'] in FIG_TYPES and not block.get('attach_to'):
                placed = page_of.get(bid)
                if placed and placed > page:
                    candidate = block
                    break
        if candidate is None:
            continue
        cid = candidate['id']
        if paper.layout.get('figure_side', True) and variants.get(cid, {}).get('mode') != 'right':
            choices = []
            for scale in paper.layout['figure_side_scales']:
                height, column = side_geometry(paper, candidate, paragraph, scale)
                if height is None or height > blank-2:
                    continue
                choices.append((scale, height, column))
                break
            if choices:
                scale, height, column = choices[0]
                variants[cid] = {'mode': 'right', 'scale': scale}
                changed = True
                continue
        current = deferred.get(cid, 0)
        if current < max_hops and _anchor_exists(paper, cid, current+1, deferred):
            deferred[cid] = current+1
            changed = True
    if not changed:
        return None
    return {'variants': variants, 'deferred': deferred}


def _anchor_exists(paper, block_id, hops, deferred):
    """True when the ``hops``-th following non-deferred figure block exists."""
    blocks = paper.blocks
    index = next(i for i, b in enumerate(blocks) if b['id'] == block_id)
    remaining = hops
    for later in blocks[index+1:]:
        if (later['type'] in FIG_TYPES and not later.get('attach_to')
                and later['id'] not in deferred):
            remaining -= 1
            if remaining <= 0:
                return True
    return False


def preflight(paper):
    typography = Typography(paper)
    for b in paper.blocks:
        typ = b['type']; size = paper.layout['body_size_pt']
        strings = [b.get('text', ''), b.get('caption', ''), b.get('notes', '')]+b.get('entries', [])
        if typ in {'figure','table'}:
            strings.append('表内文字对应：' if typ=='table' else '图中说明：')
        for text in strings:
            typography.format(text, cn='kai' if typ in {'abstract','small','figure','table'} else 'song', size=size)
    typography.assert_coverage()
    for a in paper.assets.values():
        with PILImage.open(paper.asset_path(a)) as im:
            if im.format != 'PNG':
                raise ValueError(f"Expected lossless PNG: {a['id']}")
            # One or two pixels of PDF renderer rounding are expected.
            dpi = a.get('dpi', 600)
            if abs(im.width-a['width_pt']*dpi/72)>2 or abs(im.height-a['height_pt']*dpi/72)>2:
                raise ValueError(f"Crop pixel dimensions/DPI do not match bbox: {a['id']}")
    return {'passed': True, 'fonts': typography.files, 'blocks': len(paper.blocks), 'assets': len(paper.assets)}


def build_pass(paper, blocks, variants, font, sty, target, receipt_path, sample=False):
    paper.work.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(f'Output exists: {target}; choose a new version/work_dir')
    target.parent.mkdir(parents=True, exist_ok=True)
    log = {'images': [], 'line_issues': [], 'page_sizes': {}, 'figure_layout': {}}
    expected = []; story = []; templates = []
    W = paper.layout['width_pt']; H = paper.layout['height_pt']; margin = paper.layout['margin_pt']

    def footer(canvas, doc):
        canvas._paper_log = log
        w,h = canvas._pagesize
        log['page_sizes'][str(doc.page)] = [w,h]
        # A marked PDF artifact, not a guessed leading/trailing number.
        canvas._code.append('/Artifact BMC')
        canvas.saveState(); canvas.setFont(font.fonts['roman'], 9)
        canvas.drawCentredString(w/2, 24, str(doc.page))
        canvas.restoreState(); canvas._code.append('EMC')

    def template(name, w, h):
        return PageTemplate(id=name, pagesize=(w,h), frames=[Frame(margin, margin,
            w-2*margin,h-2*margin,leftPadding=0,rightPadding=0,topPadding=0,bottomPadding=0)],onPage=footer)

    templates.append(template('normal', W, H))

    def paragraph(text, style, bid, cn=None, record=True):
        cn = cn or ('kai' if style in {'abstract','small','caption'} else 'song')
        markup, plain = font.format(text, cn=cn, bold=style in {'heading','title_en','title_cn'}, size=sty[style].fontSize)
        if record:
            expected.append({'id': bid, 'text': plain})
        return TrackedParagraph(markup, sty[style])

    def measuring(text, style, bid, cn=None):
        return paragraph(text, style, bid, cn=cn, record=False)

    pending_heading = None
    for b in blocks:
        typ = b['type']; bid = b['id']
        if b.get('attach_to'):
            # Emitted inside its parent figure's caption column, never on its own.
            continue
        if typ in FIG_TYPES:
            heading = pending_heading
            pending_heading = None
            if heading is not None:
                if not story or story[-1] is not heading:
                    heading = None
                else:
                    # Take the heading out of the story: it goes back in as part
                    # of the figure group below.  The group already guarantees
                    # they stay together, so the nested keepWithNext must go,
                    # otherwise ReportLab re-emits the heading a second time.
                    story.pop()
                    heading.keepWithNext = False
            spec = variants.get(bid, {})
            wanted = spec.get('mode', 'below'); scale = spec.get('scale')
            probe, probe_image, probe_mode = figure_group(paper, b, measuring, mode=wanted, scale=scale)
            probe_height = group_height(paper, probe)
            if probe_image.drawWidth > W-2*margin+.01 or probe_height > H-2*margin:
                if paper.layout['oversize'] != 'page':
                    raise ValueError(f"Asset and caption need a larger page: {b['asset']}")
                mode = 'below' if wanted == 'right' else wanted
                parts, image, mode = figure_group(paper, b, paragraph, mode=mode, scale=scale)
                needed_height = group_height(paper, parts)
                name = f'asset_{bid}'
                w = max(W, image.drawWidth+2*margin)
                h = max(H, needed_height+2*margin+2)
                templates.append(template(name,w,h))
                if heading is not None:
                    story += [NextPageTemplate(name), PageBreak(), heading, *parts,
                              NextPageTemplate('normal'), PageBreak()]
                else:
                    story += [NextPageTemplate(name), PageBreak(), *parts,
                              NextPageTemplate('normal'), PageBreak()]
            else:
                parts, image, mode = figure_group(paper, b, paragraph, mode=probe_mode, scale=scale)
                needed_height = probe_height
                if heading is not None:
                    # The heading introduces this figure; never leave it alone on
                    # the previous page.
                    story.append(KeepTogether([heading, *parts]))
                else:
                    story.append(KeepTogether(parts))
            log['figure_layout'][bid] = {'mode': mode,
                                         'scale': round(image.scale, 3),
                                         'height_pt': round(needed_height, 2)}
        elif typ == 'references':
            pending_heading = None
            if b.get('text'):
                story.append(paragraph(b['text'], 'heading', bid+':heading'))
            for i,entry in enumerate(b['entries']):
                story.append(paragraph(entry, 'references', f'{bid}:{i+1}'))
        else:
            pending_heading = None
            for i,text in enumerate(b['text'].splitlines()):
                if text.strip():
                    story.append(paragraph(text, typ, f'{bid}:{i+1}'))
                    if typ == 'heading':
                        pending_heading = story[-1]
    while story and isinstance(story[-1], (PageBreak, NextPageTemplate)):
        story.pop()
    rank = {b['id']: i for i, b in enumerate(paper.blocks)}
    figure_sequence = [b['id'] for b in blocks
                       if b['type'] in FIG_TYPES and not b.get('attach_to')]
    log['figure_order'] = {
        'emission': figure_sequence,
        'monotonic': all(rank[a] < rank[b] for a, b in zip(figure_sequence, figure_sequence[1:])),
    }
    font.assert_coverage()
    doc = BaseDocTemplate(str(target), pagesize=(W,H), title=paper.config.get('title',''),
        author=paper.config.get('author',''), pageCompression=1)
    doc.addPageTemplates(templates)
    with chinese_wrapping():
        doc.build(story)
    receipt = {'schema_version': 1, 'sample': sample, 'pdf_sha256': digest(target),
        'input_sha256': paper.fingerprint(), 'expected_text': expected, 'layout': log,
        'font_sha256': {role: digest(path) for role,path in font.files.items()},
        'block_ids': [b['id'] for b in blocks]}
    write_json(receipt_path, receipt)
    if log['line_issues']:
        raise ValueError('Line-start/end punctuation failure; PDF retained for diagnosis, not ready for delivery')
    return receipt


def build(paper, sample=False):
    preflight(paper)
    font = Typography(paper); sty = styles(paper, font.fonts)
    if sample:
        selected = paper.config.get('sample_ids')
        if not selected:
            raise ValueError('Set sample_ids: title, actual abstract, script-bearing paragraph, and caption/figure')
        blocks = [b for b in paper.blocks if b['id'] in selected]
        if set(selected) != {b['id'] for b in blocks}:
            raise ValueError('Unknown sample_ids')
        target = paper.work/'font_sample.pdf'
        receipt = build_pass(paper, blocks, {}, font, sty, target,
                             paper.work/'sample_receipt.json', sample=True)
        return {'output': str(target), 'pages': len(receipt['layout']['page_sizes']), 'sample': True}

    by_id = {b['id']: b for b in paper.blocks}
    state = {'variants': {}, 'deferred': []}
    best = None; history = []
    passes = max(1, int(paper.layout['reflow_passes']))
    scratch_dir = paper.work/'passes'
    for number in range(1, passes+1):
        order = emission_order(paper, state['deferred'])
        blocks = [by_id[bid] for bid in order]
        target = scratch_dir/f'pass{number}.pdf'
        if target.exists():
            target.unlink()      # module scratch, regenerated on every run
        receipt = build_pass(paper, blocks, state['variants'], font, sty, target,
                             scratch_dir/f'pass{number}_receipt.json')
        blanks = measure_blanks(target, paper.layout['margin_pt'])
        score = blank_score(paper, blanks)
        history.append({'pass': number, 'score': round(score, 1),
                        'max_blank_pt': round(max_blank(blanks), 1),
                        'variants': state['variants'], 'deferred': state['deferred'],
                        'blank_pages': {str(k): round(v['blank'], 1) for k, v in blanks.items()}})
        if best is None or score < best['score']-0.01:
            best = {'score': score,
                    'variants': json.loads(json.dumps(state['variants'])),
                    'deferred': dict(state['deferred'])}
        def paragraph(text, style, bid, cn=None):
            cn = cn or ('kai' if style in {'abstract','small','caption'} else 'song')
            markup, plain = font.format(text, cn=cn, bold=style in {'heading','title_en','title_cn'}, size=sty[style].fontSize)
            return TrackedParagraph(markup, sty[style])
        nxt = plan_reflow(paper, state, blanks, receipt, paragraph, by_id, order)
        if nxt is None:
            break
        state = nxt

    order = emission_order(paper, best['deferred'])
    blocks = [by_id[bid] for bid in order]
    target = paper.output
    receipt = build_pass(paper, blocks, best['variants'], font, sty, target,
                         paper.work/'build_receipt.json')
    blanks = measure_blanks(target, paper.layout['margin_pt'])
    receipt['layout']['variants'] = best['variants']
    receipt['layout']['deferred'] = best['deferred']
    receipt['layout']['reflow'] = history+[{'pass': 'final', 'score': round(blank_score(paper, blanks), 1),
                                            'max_blank_pt': round(max_blank(blanks), 1),
                                            'blank_pages': {str(k): round(v['blank'], 1)
                                                            for k, v in blanks.items()}}]
    receipt['layout']['blank_pages'] = {str(k): round(v['blank'], 1) for k, v in blanks.items()}
    write_json(paper.work/'build_receipt.json', receipt)
    return {'output': str(target), 'pages': len(receipt['layout']['page_sizes']),
            'sample': False, 'passes': len(history), 'variants': best['variants'],
            'deferred': best['deferred'], 'max_blank_pt': round(max_blank(blanks), 1)}
