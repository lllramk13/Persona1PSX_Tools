# P1 汉化·接力手册（写给自己动手的你）

> 配套阅读：`README.md`（所有结论性事实）、`docs/RE_Walkthrough_EBIN.md`（逆向过程复盘）。
> 本文包含：现状 → 目标路线图 → 原理速记 → 工具手册 → 详细 TODO → 练习题 → 踩坑守则
> → **§7 现有积木 API 速查** → **§8 未来工具设计规格书**（每个要写的工具：目的/输入输出/
> 伪代码/用哪些现有函数/验证清单/坑，照着写就行）。

---

## 0. 一页纸现状（2026-07-02）

| 事项 | 状态 |
|---|---|
| 文本编码 / TALK / E / D / SLPS 提取 | ✅ 全破，20110 条进 `all_text.json` |
| E 脚本虚拟机（opcode 表、长度表、指针清单） | ✅ 反汇编全破（README §3.2b） |
| **任意长度回插**（`Text_inject/ebin_rebuild.py`） | ✅ byte-identity 全过 + **实机验证通过** |
| 实机实验盘 `[E0-grow-test]` | ✅ 开场对白 +8 字节，`0123` 正常渲染 |
| 翻译进度 | 387 / 18959（全在 E0，2%） |
| 术语表 | 7877 候选，0 已定名 ⚠ |
| 中文字库新方案（追加式码表） | ⬜ 未开工（旧字频 hack 已作废，别再用） |
| 批量回插管道 | ⬜ 未开工（本手册重点） |
| 镜像扩容层（E 文件变大时） | ⬜ 未开工（gen_index.py 已备好一半） |
| git | ⚠ **大量成果未提交，第一件事就是 commit** |

---

## 1. 大目标与路线图

**终极目标**：出一张完整中文化的 P1 游戏盘。

```
里程碑 M1  逆向地基            ██████████ 100%  ← 你在这
里程碑 M2  中文字库+批量管道    ░░░░░░░░░░   0%  ← 下一步
里程碑 M3  E0 全中文测试盘      ░░░░░░░░░░   0%
里程碑 M4  镜像扩容层           ░░░░░░░░░░   0%
里程碑 M5  全文本翻译+校对      █░░░░░░░░░   2%
里程碑 M6  TALK/D/SLPS/图形     ░░░░░░░░░░   0%
```

M2→M3 是当前主线。M5（翻译产能）可以并行推进——**翻译不依赖任何代码**，
你随时可以翻；管道好了译文就能进游戏。

---

## 2. 核心原理速记（考试要点版）

### 2.1 文本编码（背下来）

一句话 = 一串字库槽位编号。三条规则：

| 字节 | 含义 |
|---|---|
| `0x00-0x7F` | 单字节，slot = 字节本身 |
| `0x80-0x87` + XX | 两字节，slot = `(b-0x80)*256+XX`（**写入时 slot≥0x80 一律用这个**） |
| `0xFF` + XX | 控制码（FF01 关框 / FF02 等待 / FF03 换行 / FF04 清框…见 README §1.2） |

### 2.2 段模型（这次突破的核心）

- 每个 E 文件 = 扇区 0 的 u16 目录 + 若干段；**段整体加载到 RAM `0x80100000`**。
- 段内一切交叉引用 = **4 字节对齐的绝对地址 `0x8010xxxx`**（没有相对偏移）。
  换算：`段内偏移 = 指针值 − 0x80100000`。
- 引擎代码只硬编码段偏移 `< 0x10C8`（段头+元数据）→ **[0, 0x10C8) 不能动，
  之后随便搬，只要把指针改对**。
- 这就是"任意长度"的全部原理：**平移字节 + 改通讯录**。像链接器做重定位。

### 2.3 脚本 VM（读得懂 --units 输出就行）

- 脚本 = 4 字节对齐记录 `FF op p1 p2 [...]`；每个 op 的长度查 ADV.BIN 里的表
  （文件偏移 0x568F0）。
- 带指针的 opcode 只有 7 个：FF22/23（跳转,+4）、FF38/3E/49（条件跳转,+8）、
  **FF55（显示文本,+4）**、FF58（存文本指针,+4）。
- FF21 = 脚本结束。文本是"离线"的：FF55 指着它，播完从 FF55 记录后继续。
- **文本单元**：dump reader 的一个大 span 实为多个背靠背单元，每个被指针独立引用，
  选项分支的 resume 指针可以指到句子中间。重建按"指针目标切分点"分单元。

### 2.4 重建器四步（ebin_rebuild.py 内部）

```
①解析: 切段 → 扫指针（跳过文本区，文字会巧合成 0x8010xxxx！）→ 指针目标切单元
②布局: 固定前缀原样 → 按原顺序排块，被替换单元补 00 到 mod-4 同余
③重定位: 每个指针字按新布局改写
④收尾: 段尾去零 → 补到 0x800 倍数 → 重写 u16 目录
```

### 2.5 显示层几何（排版器要用）

- 光标每字 +1；FF03 换行推到下一个 15 的倍数，但列寻址按 mod 16 → **每行只写 ≤14 字**。
- FF01/FF02 有脚本侧状态机效应 → **数量顺序绝对不能增删**；FF03 是纯光标 → 可自由增删。

---

## 3. 工具箱使用手册

```bash
cd P1_Tools

# ── 提取/查看 ──────────────────────────────────────────────
python3 Text/dump.py extrac/ADV/E0.BIN -o /tmp/e0.json      # dump 文本
python3 Text/dump.py extrac/ADV/E0.BIN --render /tmp/v.png  # 渲字模肉眼看
python3 Text/export_all.py                                  # 重新汇总 all_text.json

# ── 重建器（这次的新核心） ─────────────────────────────────
python3 Text_inject/ebin_rebuild.py --verify        # 恒等自测（改完代码必跑！）
python3 Text_inject/ebin_rebuild.py --units E0 0    # 看段 0 的文本单元列表

# ── 实验范例（改文本→自检→出盘 的完整姿势，抄它） ──────────
python3 Text_inject/exp_grow_e0s0.py

# ── 逆向工具（本次会话抢救进仓库的） ──────────────────────
python3 re_tools/adv.py dis 0x800ab874 20   # 反汇编 ADV.BIN 任意地址
python3 re_tools/adv.py table 0x800654fc 8  # 按 u32 dump 数据表
python3 re_tools/validate_script.py         # 816 段全量静态验证

# ── 翻译侧 ─────────────────────────────────────────────────
python3 Text_inject/sync_json.py            # 合并翻译批次（干跑）
python3 Text_inject/sync_json.py --apply    # 真写入（自动备份 .bak）
```

**用 ebin_rebuild 手改一句文本的最小代码**（放个 py 文件在 Text_inject/ 下）：

```python
from ebin_rebuild import EFile
ef = EFile("../extrac/ADV/E0.BIN")
sec = ef.section(0)                    # --units 先看好单元边界
a, b = 0x1D00, 0x1D54                  # 要改的单元
new = sec.raw[a:b] + b"\x80\xc0"       # 例：尾部加个 '0'（slot 192 = 80 C0）
data = ef.rebuild({0: {a: new}})       # 全部指针自动修好
open("out/E0.test.BIN", "wb").write(data)
# 等长时可直接用 build_test_disc 的 write_user_data 写盘（LBA 87074）
# ⚠ 段内容不能超过该段现有扇区配额；exp_grow_e0s0.py 里有完整自检代码，抄它
```

---

## 4. 详细 TODO（按建议顺序；标 ★ 的适合你独立完成）

> 每一项要写的代码，**完整设计规格（输入输出/伪代码/用哪些现有函数/怎么验证）在 §8**；
> 现有积木的函数签名速查在 §7。写代码前先看这两节。

### T0 ★ git commit（10 分钟，现在就做）

```bash
git add -A && git commit -m "E-script VM cracked; arbitrary-length rebuild verified in-game"
```
本次成果（脚本 VM、重建器、两份 docs、re_tools）全在工作区没提交，先落袋。

### T1 中文字库：追加式码表 + 字形写入（M2 第一块，难度 ★★☆）

**目标**：一个 `font_append.py`——输入若干中文字，输出 patch 后的 FONT.BIN 和
追加了新字的 codetable JSON。**只追加，不动任何原槽位**（码表=存档 ABI）。

**做法**：
1. 找空槽：`codetable_og.json` 里值为空/未用的槽（README §1 提到 247-251、1801 等；
   写个脚本把所有"未在任何 dump 文本中出现过的槽"统计出来更稳）；
2. 渲染 16×16 1bpp 字形：参考已废弃脚本里的 `render_glyph()`（PIL + wqy-zenhei，
   32 字节/字，2 字节一行 MSB 在左；`git show baf663e:Text_inject/test_inject_e0.py`），
   写进 `FONT.BIN` 的 `slot*32` 处；
3. 输出 `codetable_zh.json` = 原表 + 追加项；
4. 空槽不够用时（全游戏常用汉字约 2500+，空槽可能只有几百）：统计**全部翻完后**
   哪些日文假名/汉字槽不再被任何文本引用，做"腾退名单"分期征用——这一步等
   翻译量上来再说，先用空槽跑通 E0。

**验证**：dump.py --render 用新码表渲一句含新字的文本，肉眼确认字形。

**你会学到**：字库格式、位图打包、码表 ABI 约束的工程意义。

### T2 粒度桥接：translations.json → 单元字节（M2 第二块，难度 ★★★）

**目标**：`unit_encode.py`——把一条翻译条目（span 粒度，zh 带 ⟦n⟧）切分成
若干文本单元的新字节。

**核心问题**：翻译条目 = reader span（大），重建单元 = 指针切分（小，见 §2.3）。
好在**控制码 ⟦n⟧ 与原文一一对应**，可以这样对位：
1. 解码原 span 成 token 流（`decode.py`），记下每个 token 的字节偏移；
2. 单元边界（来自 `Section.units`）落在哪两个 token 之间是确定的；
3. zh 里的 ⟦n⟧ 序号与原 token 流的控制码一一对应 → 在 zh 里找到对应边界点切开；
4. 每个单元段用 `encode.py` 的 `markup_to_tokens`/`encode_tokens` 编码
   （查 T1 的新码表，slot≥0x80 自动逃逸）。

**边界情况**：单元边界切在句子中间（选项 resume）时，zh 的切分点语义要和原文一致
——初版可以先只处理"边界恰在控制码上"的单元（绝大多数），其余报错跳过，统计有多少。

**验证**：对已翻的 387 条跑一遍，报告"可自动切分/需人工"比例。

**你会学到**：token 化、双语对齐、encoder 内部。

### T3 排版器（M2 第三块，难度 ★★☆）

**目标**：编码前对 zh 自动断行——每行 ≤14 字；FF01/FF02 原序原数；FF03 可增删。
输入"每页原来有几行"（从原文 token 流数出来），输出断好行的 zh。
初版规则可以很笨：逐字累计，满 14 插 `\n`；页内行数超原页就压缩或报警。

**验证**：渲染预览（参考 `git show baf663e:Text_inject/test_inject_e0.py` 的 `render_line`）+ 实机抽查，
确认没有 `0/123?` 那种折行。

### T4 首张中文 E0 测试盘（M3，把 T1-T3 串起来，难度 ★★☆）

**目标**：`build_zh_disc.py`——读 translations.json 里所有 E0 已翻条目 →
T2 切分编码 → `ebin_rebuild` 重建 → 若每段都没超扇区配额则等长写盘。
超配额的段先跳过并统计（等 T6）。

**验证清单**：`--verify` 恒等 → 静态重 dump 对比 → 实机开场全流程 + 选项两边都点。

### T5 ★ 翻译与术语表（M5，随时可做，不依赖代码）

1. **先定术语**：`glossary_candidates.json` 7877 项按 count 降序，把前几百个高频
   专名的 `zh` 填了（人名/恶魔名/地名/道具名优先）——这决定全部译文的一致性；
2. 继续翻 E0（1170 条里还剩 783）：批次文件放 `Text_inject/translations/`，
   格式 `{id: {jp, zh}}`，`sync_json.py` 干跑校验 → `--apply` 合并；
   占位符 ⟦n⟧ 必须 0..N-1 各一次按升序，工具会帮你查。
3. 同一句翻一次顶多处（29% 重复自动扩散）。

### T6 镜像扩容层（M4，翻译量大了才需要，难度 ★★★）

**目标**：E*.BIN 变大时的写盘路径。方案（README §3.2b 已记）：
新 E0 追加到镜像尾部新扇区（其他文件 LBA 全不动）→ 改 ISO9660 目录记录里
E0 的 extent LBA + size → `gen_index.py` 重扫成品盘重建 FSECT/FSIZE 并 `--patch` 灌回。
DuckStation 不校验 EDC/ECC；真机/发布前再补算。

**验证**：先做一个"只搬不改"的实验——E0 原样追加到尾部、目录改指过去，
游戏应完全正常；再上真改动。

### T7 其他容器任意长度（M6，难度 ★★）

- **TALK**：结构最简单（头 5 u32 + 绝对/相对指针表），照 ebin_rebuild 思路写个
  talk_rebuild，正好检验你学会了没有 ←★ **推荐当你的毕业作业**；
- **D 文件**：顶层 u32 指针表已破，含文本资源整体搬尾部；
- **SLPS**：字符串重定位到空闲区 + 改 RAM 指针表（先把变长技能名表建模）。

---

## 5. 练习题（从易到难，学习用）

1. **读单元**：`python3 Text_inject/ebin_rebuild.py --units E0 5`，对照
   `dump.py` 的输出，找出哪个单元是选项 resume（起点切在句子中间的那种）。
2. **改一句话**：照 §3 的最小代码，把开场某句加个字，出盘实机看。
   再试试**删**两个字节（缩短也要 mod-4 同余，想想为什么补 00 就行）。
3. **读汇编**：`python3 re_tools/adv.py table 0x800654fc 106` 拿到全部 handler 地址，
   挑一个没分析过的（比如 FF24 @ 0x800ab8cc）`dis` 出来，对照长度表猜它读了什么操作数。
   答案可以拿 `validate_script.py` 的 opcode 频次去交叉验证。
4. **加统计**：给 `re_tools/validate_script.py` 加一个输出——每个段的
   "内容结束位置 vs 段扇区配额"，得到全 816 段的**扇区富余表**（T4 会直接用到！）。
5. **毕业作业**：写 `talk_rebuild.py`（T7），29 个 TALK 文件 byte-identity 全过为合格。

---

## 6. 踩坑守则（背下来）

1. **码表 = 存档 ABI**：只追加，永不重排/复用已用槽位；
2. **[0, 0x10C8) 固定前缀**一个字节不能动；
3. 替换单元**新长度 ≡ 原长度 (mod 4)**（rebuild 会自动补，但你要理解为什么）;
4. **FF01/FF02 原序原数**，FF03 随意，每行 ≤14 字；
5. slot ≥ 0x80 一律 `80 XX` 逃逸（0x88-0xFE 是别名逃逸头，写入禁用）；
6. 指针重定位**跳过文本区**（文字会巧合成 0x8010xxxx）；
7. 每次改完重建器相关代码，先跑 `--verify` 恒等测试再干别的；
8. 实机测试**从冷启动进场景**，不要用 savestate（段已在 RAM 里，绕过光盘）；
9. 改盘永远从干净原盘重建，别在旧测试盘上叠补丁。

## 7. 现有积木 API 速查（写新代码前必读）

所有新工具都是在这些已验证的函数上搭积木。**token** 是贯穿全部代码的中间表示，
只有两种：`("char", slot)` 和 `("ctrl", code, params:bytes)`。

```python
# ── 控制码配置（几乎每个函数都要传 ctrl） ──────────────────────
import dump                                # Text/dump.py
ctrl = dump.load_format()["ctrl"]["efile"] # {code:int -> (名字:str, 整条长度:int)}

# ── 解码 Text/decode.py ───────────────────────────────────────
decode(data, start, end, ctrl) -> [token]  # 磁盘字节 → token 流
tokens_to_text(tokens, codetable, ctrl)    # → 可读文本（调试/预览用）

# ── 编码 Text/encode.py ───────────────────────────────────────
load_codetable(path=None)                  # → {slot:int -> 字:str}
encode_slot(slot) -> bytes                 # ≤0x7F 单字节；否则 80 XX 逃逸（自动）
encode_tokens(tokens) -> bytes             # token 流 → 磁盘字节（无损）
tokens_to_markup(tokens, ct, ctrl) -> str  # token → 人可编辑标记文本（<wait>/{slot}/\n）
markup_to_tokens(text, ct, ctrl) -> tokens # 反向；查不到的字抛 EncodeError ← 天然缺字检测
tokens_to_masked(tokens, ct, ctrl)         # → (jp, masked, codes)  翻译视图
restore_masked(masked_zh, codes) -> markup # zh 的 ⟦n⟧ → 还原成真控制码标记
# ⚠ tokens_to_masked 把"空字形 slot"也当布局码进 codes——⟦n⟧↔token 的对应
#   关系必须以它的实现为准（对齐时直接调它拿 codes，别自己数控制码）。

# ── E 容器 Text/containers.py ─────────────────────────────────
read_efile(data) -> [(scene, text_start, text_end)]   # 文件级偏移的文本 span

# ── 段重建 Text_inject/ebin_rebuild.py（本项目核心） ──────────
ef  = EFile(path)          # 解析整个 E 文件
ef.sections                # [(start,end)] 各段的文件偏移
ef.sec_spans[i]            # 段 i 的文本 span 列表（段内偏移）
sec = ef.section(i)        # → Section
sec.units                  # [(a,b)] 文本单元（指针目标切分，段内偏移）
sec.ptr_sites              # [(site, target)] 每个指针字的位置和目标
ef.rebuild({i: {unit_start: new_bytes}}) -> bytes   # 新 E 文件；指针全自动修
# new_bytes 不用自己补齐，rebuild 会补 00 到与原单元 mod-4 同余

# ── 写盘 Text_inject/build_test_disc.py（当库用） ─────────────
read_user_data(disc_path, lba, size) -> bytes   # 按 LBA 读用户数据(2048/扇区)
write_user_data(disc_path, lba, data)           # 原位写（文件必须等长！）
DISC_SOURCE / DISC_DIR                          # 干净原盘路径 / 输出目录
# 关键 LBA：ADV/E0.BIN = 87074，FONT.BIN = 602（65536B = 32 扇区）

# ── 镜像索引 gen_index.py（扩容层用） ─────────────────────────
Disc(path)                 # raw 2352 镜像按 LBA 读写；自动探测用户数据偏移
scan_files / build_tables  # 重扫 ISO 目录树 → 重建 FSECT/FSIZE.DAT

# ── 逆向 re_tools/ ────────────────────────────────────────────
adv.py dis <RAM地址> [条数]   # 反汇编 ADV.BIN（文件偏移 = RAM − 0x800643A0）
adv.py table <RAM地址> <条数> # 按 u32 dump 数据表
validate_script.py            # 816 段脚本图遍历全量验证
```

**万能恒等测试思想**（每个新工具都要有自己的版本）：把日文原文当作"译文"走一遍
你的新管道，输出必须和原始字节完全一致。过了这关，说明管道本身无损，之后出问题
只会出在译文内容上。

## 8. 未来工具设计规格书（照着写就行）

整条管道的数据流（对应 TODO 编号）：

```
Text_inject/translations/*.json（人工批次）
        │ sync_json.py --apply
        ▼
Text/translations.json（zh 带 ⟦n⟧） + Text/all_text.json（span/codes）
        │
        ▼
┌──[T2 unit_encode.py]── 单元边界来自 ebin_rebuild.Section.units
│       │ 内部先过 [T3 layout.py] 断行，再查 [T1] 的新码表编码
│       ▼
│  {段号: {unit_start: new_bytes}}
│       ▼
│  ebin_rebuild.EFile.rebuild → E0.zh.BIN
│       ▼
└──[T4 build_zh_disc.py] ── 段没超扇区配额 → LBA 原位写盘
                            超了 → [T6 disc_expand.py]（镜像尾部追加）
        ▼
   DuckStation 实机验证
[T1 font_append.py] 独立支线：FONT.zh.BIN + codetable_zh.json（T2/T4 消费）
```

---

### 8.1 font_append.py（T1，~150 行，难度 ★★☆）

**目的**：把译文用到的中文字**追加**进字库和码表；绝不动任何已用槽位（存档 ABI）。

**输入**：`extrac/FONT.BIN`、`Codetable/codetable_og.json`、`Text_inject/wqy-zenhei.ttc`、
需求字集合（从 `translations.json` 全部 zh 统计）。

**输出**：`out/FONT.zh.BIN`、`out/codetable_zh.json`、`out/font_report.json`（分配清单）。

**逻辑**：
1. **收集需求字**：遍历所有 zh → `restore_masked` 还原 → 去掉控制码标记/`{slot}`/换行
   → unique 字集合；
2. **减掉已有字**：码表里已有的字直接复用原槽（日文汉字与中文**同形**才能复用，
   如"未来/自分"；简繁不同形的不能，如 体≠體——保守起见先精确匹配字符串）；
3. **盘点空槽**：权威方法 = 收集 `all_text.json` 全部 jp 实际引用过的 slot（含 codes 里的
   `{slot}`）∪ 码表值非空的槽 → 其余 = 可用空槽；初期 E0 需求几百字，空槽应该够；
   不够时的"腾退名单"（未被引用的假名槽）等翻译量上来再做，报告里先统计出来；
4. **渲字形**：PIL `ImageFont.truetype(ttc, 12)`，textbbox 居中到 16×16，阈值二值化
   → 32 字节（每行 2 字节，MSB 在左）。实现参考
   `git show baf663e:Text_inject/test_inject_e0.py` 的 `render_glyph()`；
5. 写入 `font[slot*32:(slot+1)*32]`；输出码表 = 原表 + 追加项。

**验证**：① `Codetable/extract_codetable.py` 对新 FONT 重渲 font_grid.png 肉眼看；
② 用新码表 `dump.py --render` 渲一句含新字的句子；③ 断言：新码表与原码表在
所有原有槽位上逐项相同（ABI 守护测试，写进代码里）。

**坑**：字太细渲出来会断笔画——阈值(96)和字号(12)是调过的起点，改前先渲几个字看。

---

### 8.2 unit_encode.py（T2，~200 行，难度 ★★★，管道的心脏）

**目的**：把一条翻译条目（span 粒度）切成引擎单元粒度并编码：
`(all_text 条目, zh) → {unit_start(段内偏移): new_bytes}`。

**为什么需要**：翻译条目 = reader 的大 span，但引擎按指针引用更细的单元（§2.3），
FF55/resume 指针必须指到正确的新位置——所以必须按 `Section.units` 的边界切开、
逐单元交给 `rebuild`（它只重定位单元**起点**）。

**逻辑**：
1. 解码原 span：`tokens = decode(data, a, b, ctrl)`，同时**自己算每个 token 的字节偏移**
   （decode 不返回偏移；char slot≤0x7F 占 1 字节否则 2，ctrl 查 `ctrl[code][1]`——
   20 行的小函数，练习题材料）；
2. 取落在本 span 内的单元边界 `cuts = [u for (u,_) in sec.units if a < u < b]`；
   每个 cut 必须正好落在某 token 的起点上（不落 = 结构异常，报错并统计，先跳过）；
3. **⟦n⟧ 对齐**：对原 tokens 调 `tokens_to_masked` 拿到 codes 顺序——zh 里的 ⟦n⟧
   与之一一对应。据此能算出"第 k 个单元覆盖 zh 的哪一段"（cut 落在第 m 个 code 前
   → zh 在 ⟦m⟧ 前切开）；
4. 每段：`restore_masked(zh_seg, codes_seg)` → （接 T3 排版）→
   `markup_to_tokens(seg, ct_zh, ctrl)` → `encode_tokens` → bytes。
   缺字抛 `EncodeError` → 收集起来喂给 font_append 再跑一轮；
5. 不用管 mod-4 补齐，`Section.rebuild` 会做。

**验证（必做）**：恒等测试——把 jp 自己当 zh 走完整管道，`encode_tokens` 出来的
字节必须 == 原 span 字节，387 条全过再上真译文。

**坑**：⟦n⟧ 的语义以 `tokens_to_masked` 实现为准（空字形 slot 也算占位符），
自己数控制码会错位。

---

### 8.3 layout.py（T3，~120 行，难度 ★★☆）

**目的**：编码前对每个单元的**还原后 markup**（真换行、真控制码那一层，不是 masked 层）
自动断行排版。

**规则**（§2.5 的工程化）：
- `\n`（=FF03）可自由增删；`<close>/<wait>/<clear>/<color:..>/<pause:..>` 等
  **原序原数绝不动**；
- 每行可见字符 ≤14（`{slot}` 算 1 格；控制码 0 格）；
- 数出原单元每页（相邻 close/wait 之间）的行数，新排版每页行数尽量不超原页，
  超了打警告清单（人工精简译文）。

**逻辑**：把 markup 解析成 `[文字块|控制码]` 序列（可以 `markup_to_tokens` 再遍历）→
删掉旧 `\n` → 文字按 14 字/行贪心重折 → 按页行数预算插回 `\n` → 拼回 markup。

**验证**：日文原文过一遍（恒等冒烟，断行位置可能变但页结构必须不变）；
渲染 PNG 预览抽查；实机看有没有 `0/123?` 式断字。

**坑**：一定工作在 `restore_masked` 之后的层——在 masked 层增删换行会破坏
⟦n⟧ 编号校验。

---

### 8.4 build_zh_disc.py（T4，~150 行，难度 ★★☆，一键总装）

**目的**：`translations.json` → 中文测试盘，一条命令。

**流程**：
1. 收集 E0 已翻条目（有 zh 的），按段分组；
2. 逐条过 T2（内含 T3）→ 汇总 `{sec: {unit: bytes}}`；失败条目列清单继续；
3. `ef.rebuild(...)` → 新 E0；`--verify` 式自检（重 dump 对比：没翻的 span 必须
   逐字节不变）；
4. **扇区配额检查**：逐段 `内容尾 ≤ 原扇区数×0x800`？超的段整段回退成原文并
   记入"待扩容"清单（等 T6）；
5. 等长成立 → 复制干净原盘 → `write_user_data(盘, 87074, E0)` +
   `write_user_data(盘, 602, FONT.zh.BIN)` → 回读校验。

**用法**：`python3 build_zh_disc.py [--dry-run]`，输出盘 + 一份报告
（翻了多少/塞进多少/缺字多少/待扩容哪些段）。

**验证**：实机开场全流程 + 选项两边都点 + 随机进几个场景。

---

### 8.5 disc_expand.py（T6，~200 行，难度 ★★★，暂缓到需要时）

**目的**：某段超扇区配额 → E 文件变大 → 等长写盘失效时的镜像层。

**方案**（最小扰动：其他文件 LBA 全不动）：
1. 新 E0 追加到镜像**尾部**新扇区（新扇区要造 MODE2 Form1 头：12B sync +
   3B 地址 + 1B mode + 8B subheader；地址字段是 BCD 的 MSF——用 gen_index.Disc
   读几个现有扇区头照抄格式）；
2. 改 ISO9660 目录记录：用 `gen_index.Disc` 遍历目录树找到 `E0.BIN` 的目录项，
   改 extent LBA 和 size——**注意 ISO9660 的 u32 是 LE+BE 双端序各存一份，两份都要改**；
3. `gen_index.py --patch`：重扫目录树重建 FSECT.DAT/FSIZE.DAT 灌回镜像
   （游戏实际用这两张表找文件，这步才是真正生效的）；
4. EDC/ECC 先不算（DuckStation 不校验；真机/发布前再补）。

**验证路径**（分两步隔离变量）：先做"E0 原样搬尾部、不改内容"实验——游戏应
完全正常；再上真加长。

---

### 8.6 talk_rebuild.py（T7 毕业作业，~150 行，难度 ★★）

**目的**：给 TALK 容器做 ebin_rebuild 的同款：任意长度 + 指针修复。
写完它 = 你已出师。

**结构**（README §3.1）：头 5×u32 `[表偏移, 段0界, 段1界, 段2界, 段3界]`
（ETC.BIN 变体表偏移 0x04）；段 0 指针**绝对**（文件偏移），段 1 指针**相对段起点**。

**逻辑**：解析两张指针表 → 指针目标切文本单元 → 替换单元、重算布局 →
改写指针表 + 头部 5 个界值。比 E 简单得多：没有脚本 VM、没有 RAM 基址换算。

**验证**：29 个 TALK 文件 byte-identity 全过；改一句 dump 回来对比。
文件大小变化后同样受盘层约束（先在段尾余量内玩，或等 T6）。

---

## 9. 下次怎么让 AI 快速接上

新会话开头直接说：
> 读 P1_Tools/README.md 的 §3.2b、docs/NEXT_STEPS_接力手册.md 和
> docs/RE_Walkthrough_EBIN.md，我们从 T〈编号〉继续。

所有事实都在这三份文档里，不需要重新逆向任何东西。
