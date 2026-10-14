"""Behavioral regression suite. Generates only synthetic paper data in --work-dir."""
import argparse
from pathlib import Path
import json
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from paperpdf.config import Paper,write_json,read_json
from paperpdf.build import build,preflight,crop,emission_order
from paperpdf.verify import verify,render,delivery,record_review
from PIL import Image
import pdfplumber
from pypdf import PdfReader,PdfWriter
from pypdf.generic import DecodedStreamObject,NameObject

WORK=None


class Regression(unittest.TestCase):
    def make_paper(self, extra=None, blocks=None, extra_images=None):
        root=Path(tempfile.mkdtemp(prefix='case_',dir=WORK))
        for color in ['red','blue']:
            Image.new('RGB',(100,50),color).save(root/(color+'.png'))
        for name,size,color in (extra_images or []):
            Image.new('RGB',size,color).save(root/(name+'.png'))
        assets=([dict(id=color,path=color+'.png',page=1,bbox=[0,0,100,50],dpi=72) for color in ['red','blue']]
                +[dict(id=name,path=name+'.png',page=1,bbox=[0,0,size[0],size[1]],dpi=72)
                  for name,size,_ in (extra_images or [])])
        blocks=blocks or [
            dict(id='title',type='title_cn',text='可移植排版回归验证'),
            dict(id='start_number',type='body',text='1开头的正文不可被误当页码。left_right 保留字面下划线；−0.44不是0.44。'),
            dict(id='mixed',type='body',text=('中文标点，（括号不能孤立）。“引号完整”，I_TEST与Q_flux保留上下标，40°N不拆开。'*12)),
            dict(id='abstract',type='abstract',text='El Niño、λ、φ、ω、©，m s⁻¹，面积km²。'),
            dict(id='red',type='figure',asset='red',caption='图1 红色图块。',notes='red → 红色。'),
            dict(id='blue',type='table',asset='blue',caption='表1 蓝色图块。',notes='blue → 蓝色。'),
            dict(id='refs',type='references',text='参考文献',entries=['Author A (2020) A title with upper-tropospheric and DOI 10.1000/example.'])]
        write_json(root/'blocks.json',blocks);write_json(root/'assets.json',assets)
        config=dict(schema_version=1,blocks='blocks.json',assets='assets.json',output='result.pdf',work_dir='work',
            sample_ids=['title','mixed','red'],symbols={'I_TEST':{'text':'I','sub':'TEST'},'Q_flux':{'text':'Q','sub':'flux'}})
        if extra:config.update(extra)
        write_json(root/'paper.json',config)
        return Paper(root/'paper.json')

    def test_complete_text_fonts_images_and_footer(self):
        p=self.make_paper();build(p);q=verify(p)
        self.assertTrue(q['passed'],q);self.assertEqual(q['image_count'],2)
        self.assertTrue(all(x['passed'] for x in q['images_checked']))
        receipt=read_json(p.work/'build_receipt.json')
        self.assertEqual(receipt['layout']['line_issues'],[])
        self.assertIn('left_right',(p.work/'final_extracted_text.txt').read_text(encoding='utf8'))
        self.assertIn('1开头',(p.work/'final_extracted_text.txt').read_text(encoding='utf8'))

    def test_missing_font_and_glyph_fail_before_output(self):
        p=self.make_paper({'fonts':{'song':'does-not-exist.ttf'}})
        with self.assertRaisesRegex(ValueError,'Missing song'):build(p)
        self.assertFalse(p.output.exists())
        p=self.make_paper(blocks=[dict(id='bad',type='body',text='缺字检查\U0010ffff')])
        with self.assertRaisesRegex(ValueError,'Missing glyph'):preflight(p)
        self.assertFalse(p.output.exists())

    def test_relocation_and_different_cwd(self):
        p=self.make_paper();moved=p.root/'relocated skill'
        scripts=Path(__file__).resolve().parents[1]
        shutil.copytree(scripts/'paperpdf',moved/'paperpdf',ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copy2(scripts/'paper_pdf.py',moved/'paper_pdf.py')
        result=subprocess.run([sys.executable,'-X','utf8',str(moved/'paper_pdf.py'),'build',str(p.filename)],
                              cwd=WORK,capture_output=True,text=True,encoding='utf8')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue(verify(p)['passed'])

    def test_same_sized_images_swapped_are_rejected(self):
        p=self.make_paper(blocks=[dict(id=c,type='figure',asset=c,caption='图 '+c,notes='颜色对照。') for c in ['red','blue']]);build(p)
        reader=PdfReader(p.output);writer=PdfWriter()
        for pg in reader.pages:writer.add_page(pg)
        replaced=False
        for pg in writer.pages:
            data=pg.get_contents().get_data()
            names=[n for n in pg['/Resources'].get('/XObject',{}) if n.encode()+b' Do' in data]
            if len(names)>=2:
                data=pg.get_contents().get_data();a=names[0].encode();b=names[1].encode()
                data=data.replace(a,b'/TMP_SWAP').replace(b,a).replace(b'/TMP_SWAP',b)
                stream=DecodedStreamObject();stream.set_data(data);pg.replace_contents(stream)
                replaced=True;break
        self.assertTrue(replaced)
        with p.output.open('wb') as f:writer.write(f)
        q=verify(p)
        self.assertFalse(q['passed']);self.assertIn('image_identity_or_geometry',{x['kind'] for x in q['issues']})

    def test_literal_underscore_loss_is_not_normalized_away(self):
        p=self.make_paper();build(p)
        receipt=read_json(p.work/'build_receipt.json')
        # Simulate a lost glyph in the expected/observed comparison without any
        # numeric, punctuation, or underscore-tolerant normalization.
        for t in receipt['expected_text']:
            t['text']=t['text'].replace('left_right','leftright')
        write_json(p.work/'build_receipt.json',receipt)
        q=verify(p)
        self.assertFalse(q['text_exact_match'])

    def test_oversized_figure_uses_larger_page_without_scaling(self):
        p=self.make_paper({'layout':{'width_pt':180,'height_pt':240,'margin_pt':45}},
            blocks=[dict(id='large',type='figure',asset='red',caption='图1 原尺寸图片。',notes='红色。')])
        build(p);q=verify(p)
        self.assertTrue(q['passed'],q)
        receipt=read_json(p.work/'build_receipt.json');im=receipt['layout']['images'][0]
        self.assertEqual(im['width'],100);self.assertEqual(im['height'],50)
        self.assertGreater(receipt['layout']['page_sizes'][str(im['page'])][0],180)

    def test_render_cache_and_visual_gate(self):
        p=self.make_paper();build(p)
        first=render(p);second=render(p)
        self.assertFalse(any(x['cached'] for x in first['pages']))
        self.assertTrue(all(x['cached'] for x in second['pages']))
        self.assertFalse(delivery(p)['ready'])
        # This only tests state transitions; synthetic test acknowledgement is
        # not a claim that a human reviewed this fixture.
        record_review(p,'all');self.assertTrue(delivery(p)['ready'])
        Image.new('RGB',(1,1),'white').save(p.work/first['pages'][0]['image'])
        self.assertFalse(delivery(p)['ready'])

    def test_changed_inputs_invalidate_checks(self):
        p=self.make_paper();build(p)
        blocks=read_json(p.blocks_path);blocks[0]['text']='修改后的标题';write_json(p.blocks_path,blocks)
        q=verify(Paper(p.filename))
        self.assertFalse(q['passed']);self.assertIn('stale_inputs',{x['kind'] for x in q['issues']})

    def test_crop_preserves_coordinates_and_cache(self):
        p=self.make_paper();build(p)
        cfg=read_json(p.filename);cfg['source_pdf']='result.pdf'
        assets=read_json(p.assets_path)
        for a in assets:a['path']='cropped/'+a['id']+'.png'
        write_json(p.assets_path,assets);write_json(p.filename,cfg)
        fresh=Paper(p.filename)
        self.assertFalse(any(x['cached'] for x in crop(fresh)))
        self.assertTrue(all(x['cached'] for x in crop(fresh)))
        with Image.open(fresh.asset_path(fresh.assets['red'])) as im:self.assertEqual(im.size,(100,50))


    def test_tall_figure_moves_its_caption_to_the_right_column(self):
        lead='前置正文段落，用于占据版面上部空间。'*40
        notes='图中说明标签对照。'*24
        blocks=[dict(id='lead',type='body',text=lead),
                dict(id='fig',type='figure',asset='tall',caption='图1 纵向长图的中文图注。',notes=notes),
                dict(id='tail',type='body',text='后续正文。'*60)]
        p=self.make_paper(blocks=blocks,extra_images=[('tall',(150,380),'green')])
        build(p)
        receipt=read_json(p.work/'build_receipt.json')
        spec=receipt['layout']['figure_layout']['fig']
        self.assertEqual(spec['mode'],'right',spec)
        self.assertLess(spec['height_pt'],receipt['layout']['page_sizes']['1'][1]-2*56.6929)
        q=verify(p);self.assertTrue(q['passed'],q)
        image=next(x for x in receipt['layout']['images'] if x['id']=='tall')
        with pdfplumber.open(p.output) as doc:
            page=doc.pages[image['page']-1]
            box=next(im for im in page.images if abs(im['width']-image['width'])<1 and abs(im['height']-image['height'])<1)
            beside=[c for c in page.chars if c['x0']>box['x1']-1 and c['top']<box['bottom'] and c['bottom']>box['top']]
        self.assertTrue(beside,'caption text must sit beside, not under, the figure')

    def test_attached_caption_panel_rides_in_its_figure_column(self):
        lead='前置正文段落，用于占据版面上部空间。'*40
        blocks=[dict(id='lead',type='body',text=lead),
                dict(id='fig',type='figure',asset='tall',caption='图1 中文图注。',
                     notes='图中说明：坐标轴 → 数值；图例 → 颜色。'),
                dict(id='panel',type='equation',asset='panel',attach_to='fig'),
                dict(id='tail',type='body',text='后续正文。'*20)]
        p=self.make_paper(blocks=blocks,extra_images=[('tall',(150,380),'green'),('panel',(150,90),'orange')])
        build(p)
        receipt=read_json(p.work/'build_receipt.json')
        fig=next(x for x in receipt['layout']['images'] if x['id']=='tall')
        panel=next(x for x in receipt['layout']['images'] if x['id']=='panel')
        self.assertEqual(fig['page'],panel['page'])
        self.assertGreaterEqual(panel['x'],fig['x']+fig['width']-1)
        # The attachment rides in the parent column exactly once.
        self.assertEqual([im['id'] for im in receipt['layout']['images']].count('panel'),1)
        self.assertTrue(verify(p)['passed'])

    def test_wide_attachment_forces_a_wider_column_or_falls_back(self):
        # A 340pt panel does not fit the column left by scale 1.0 (319.9pt), so
        # the figure must shrink inside the allowed ladder or stack the caption.
        blocks=[dict(id='lead',type='body',text='前置正文段落，用于占据版面上部空间。'*40),
                dict(id='fig',type='figure',asset='tall',caption='图1 中文图注。',notes='图中说明：坐标轴 → 数值。'),
                dict(id='panel',type='equation',asset='panel',attach_to='fig')]
        p=self.make_paper(blocks=blocks,extra_images=[('tall',(150,380),'green'),('panel',(340,90),'orange')])
        build(p)
        receipt=read_json(p.work/'build_receipt.json')
        tall=next(x for x in receipt['layout']['images'] if x['id']=='tall')
        panel=next(x for x in receipt['layout']['images'] if x['id']=='panel')
        spec=receipt['layout']['figure_layout']['fig']
        if spec['mode']=='right':
            self.assertLessEqual(spec['scale'],0.85+1e-9)
            self.assertGreaterEqual(panel['x'],tall['x']+tall['width']-1)
            self.assertLessEqual(panel['x']+panel['width'],tall['x']+481.9)
        else:
            self.assertLess(panel['y'],tall['y'])
        self.assertTrue(verify(p)['passed'],verify(p))

    def test_deferred_figure_is_emitted_before_the_next_figure(self):
        class Fake:pass
        paper=Fake()
        paper.blocks=[dict(id='t1',type='body'),dict(id='f1',type='figure'),
                      dict(id='t2',type='body'),dict(id='t3',type='body'),
                      dict(id='f2',type='figure'),dict(id='t4',type='body')]
        self.assertEqual(emission_order(paper,['f1']),['t1','t2','t3','f1','f2','t4'])
        # With no second anchor left, a two-hop request keeps the block in place.
        self.assertEqual(emission_order(paper,{'f1':2}),['t1','f1','t2','t3','f2','t4'])
        # No non-deferred anchor is left, so both keep their original place
        # instead of drifting to the end of the document.
        self.assertEqual(emission_order(paper,['f1','f2']),['t1','f1','t2','t3','f2','t4'])
        self.assertEqual(emission_order(paper,[]),['t1','f1','t2','t3','f2','t4'])

    def test_two_hop_deferral_is_clamped_to_keep_figure_order(self):
        # A two-hop move would print f2 before f1; the guard reduces the hops
        # so the figures still appear in source order (STA-009).
        class Fake:pass
        paper=Fake()
        paper.blocks=[dict(id='t1',type='body'),dict(id='f1',type='figure'),
                      dict(id='t2',type='body'),dict(id='f2',type='figure'),
                      dict(id='t3',type='body'),dict(id='f3',type='figure'),
                      dict(id='t4',type='body')]
        order=emission_order(paper,{'f1':2})
        self.assertEqual([b for b in order if b.startswith('f')],['f1','f2','f3'])
        self.assertNotEqual(order,['t1','f1','t2','f2','t3','f3','t4'])

    def test_single_fragment_cjk_paragraph_respects_line_break_rules(self):
        # A pure-CJK paragraph parses into one font fragment; ReportLab's
        # wordSplit path would let the comma start a line.
        # 43 full-width glyphs fill the indented first line (460.9pt / 10.5pt),
        # so the comma would be the first character of line two.
        text='甲'*43+'，'+'乙'*40
        p=self.make_paper(blocks=[dict(id='body',type='body',text=text)])
        build(p)
        q=verify(p)
        receipt=read_json(p.work/'build_receipt.json')
        self.assertEqual(receipt['layout']['line_issues'],[])
        extracted=(p.work/'final_extracted_text.txt').read_text(encoding='utf8')
        self.assertNotIn('\n，',extracted)
        self.assertTrue(q['passed'],q)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--work-dir',required=True)
    args=parser.parse_args();WORK=Path(args.work_dir).resolve();WORK.mkdir(parents=True,exist_ok=True)
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(Regression)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    write_json(WORK/'test_report.json',dict(tests=result.testsRun,passed=result.wasSuccessful(),
        failures=[str(x) for x in result.failures],errors=[str(x) for x in result.errors]))
    raise SystemExit(0 if result.wasSuccessful() else 1)
