# 睡前消息·全期校对文本

基于 [bedtimenews-archive-contents](https://github.com/bedtimenews/bedtimenews-archive-contents)
多步校对（文字校对 → 音频交叉验证 → 联网事实核查）之后的最终干净版本。

- 仅 Markdown 纯文本：无图片、无字体标签、无 wiki 类标记
- 顶部 B站/YouTube 嵌入改为纯 URL 链接（官方优先；非官方补档标注「（非官方补档）」；官方已删除的注明删除）
- 语病、口癖、序词一律保留口播原貌；仅订正事实错误
- 每期末尾附「附录」：
  - **联网事实核对及信息来源**：取自校对 PR 描述的事实核查记录与来源链接
  - **事实订正**：核实后改动的事实性内容，以脚注（`[^N]`）在正文标注位置
  - **待核对**：校对中未能定案、留待复核的事项
  - 同音错录（人名/地名转写错误）已直接修复，不在附录提及

## 目录结构

```
contents/ShuiQianXiaoXi/
  0001-0100/0001.md      # 期号 4 位 padding，每 100 期一个文件夹
  0001-0100/0013.5.md    # 番外（13.5 期等）随所属百期文件夹
  1001-1100/…
  misc/                  # 无期号内容：预告、末条新闻、年终演讲、寒暑假专稿等
```

## 生成方式

`build.py` 从校对集成分支（`integration/proofread-all`）读取文稿，并抓取上游全部校对
PR 描述生成附录：

```sh
python3 build.py --force          # 全量重建（--refresh-prs 先刷新 PR 缓存）
python3 build.py --only 116 --force
```
