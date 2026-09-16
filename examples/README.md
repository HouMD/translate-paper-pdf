# 全文翻译演示

本案例使用 `translate-paper-pdf`，将一篇双栏英文大气科学论文翻译、审校并重排为可搜索的单栏中文 PDF。

## 示例文献

Chen, H., F. Teng, W. Zhang, and H. Liao, 2017: Impacts of Anomalous Midlatitude Cyclone Activity over East Asia during Summer on the Decadal Mode of East Asian Summer Monsoon and Its Possible Mechanism. *Journal of Climate*, **30**, 739–753. DOI: [10.1175/JCLI-D-16-0155.1](https://doi.org/10.1175/JCLI-D-16-0155.1).

- [英文原文，15 页](east-asian-summer-monsoon/clim-jcli-d-16-0155.1.pdf)
- [中文译文，16 页](east-asian-summer-monsoon/clim-jcli-d-16-0155.1_zh-CN.pdf)

中文标题：东亚夏季中纬度气旋活动异常对东亚夏季风年代际模态的影响及其可能机制。

## README 演示图片

| 图片 | 内容 | PDF 页码 |
| --- | --- | --- |
| [图 1](../docs/images/demo-original-title.png) | 原文标题页 | 1（期刊页码 739） |
| [图 2](../docs/images/demo-original-figure4.png) | 原文中论文图 4 所在页 | 6（期刊页码 744） |
| [图 3](../docs/images/demo-translated-title.png) | 中文译文标题页 | 1 |
| [图 4](../docs/images/demo-translated-figure4.png) | 中文译文中论文图 4 所在页 | 6 |

这四张图片均为完整页面的 180 dpi PNG。译文 PDF 内的 11 幅原图和 2 个编号公式采用 600 dpi 无损裁剪，保留源裁剪框的物理尺寸。演示图片编号与论文图号分别计数。

## 翻译检查

本次在 Windows 本地完成，未使用外部翻译平台、专业词典或联网术语核实。耗时为本次案例的实测记录，不代表其他论文的固定处理速度。

检查覆盖摘要、6 节正文、作者单位、出版信息、致谢、11 幅图、2 个编号公式和 58 条英文参考文献。最终 PDF 通过文本与排版输入一致性、字体嵌入、图像身份与原尺寸、遮挡与越界等检查；16 页全部完成目检，模块交付检查返回 `ready: true`。自动检查不替代专业内容审阅。

译文在相应位置标出了原文的年份、图注面板、滑动窗口线型、地名表述和回归符号等疑点；未将这些疑点擅自统一或删除。文中资料链接与参考文献按原文保留，未联网更新。

## 本地文件与上传

原文已从工作区外层移动至本示例目录。`east-asian-summer-monsoon/` 顶层保存英文原文和中文成品；译文内容块、裁剪图片、生成脚本及检查报告都保存在其中的 `tmp/`，由仓库 `.gitignore` 排除，仅在本地保留。两份示例 PDF 已明确加入 `.gitignore` 的例外规则，四张 README 图片也可以正常纳入版本控制。

上传时保留仓库目录结构，即可让根目录 README 的图片及 PDF 链接正常工作。此任务只整理本地文件，没有执行 GitHub 上传。

原文版权标记为 © 2017 American Meteorological Society，原 PDF 标注为开放获取内容。论文、原图及其译文的第三方内容不因随示例提供而适用本项目的代码／技能许可证。
