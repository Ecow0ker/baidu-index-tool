# 原程序静态逆向与重构映射

## 1. 样本与工具链

本次逻辑恢复以未修改的基线 PE 为准：

```text
SHA-256  414315adf6387fbea1f521c999028f7a33f4a10cba0c93a3a95ab2bc9e4a38ca
大小      6,901,760 bytes
格式      PE32 / Intel i386 / Windows GUI
编译时间  2025-02-13 14:09:54 +0800
```

分析使用 Radare2/Rabin2 6.1.8、LLVM objdump、7-Zip、UPX 和项目外层的 `analysis/pe_triage.py`。完整只读原件保存在外层 `analysis/original/`；重构工程运行时不加载原 EXE 或其 DLL。

编译器/运行库标记：

- `http://www.eyuyan.com`、`易语言`、`黑月`：易语言静态编译链。
- `彗星HTTP操作支持库`、`E2EE服务器套件会员版 3.5.241231`：HTTP/服务器支持库。
- `wkeGetCookie`、`wkeVisitAllCookie`、`wkeClearCookie` 等 312 个 `wke*` 标记，以及伴随的 `miniblink_4957_x32.dll`：原版用 Miniblink 做软件内登录。

目录中的 V2 文件与基线只有 31 个字节不同：3 个写点被 NOP，另有一个全局初值变化。协议、界面资源和数据逻辑相同，因此协议重构以基线文件为唯一取证对象。

## 2. 主工作流

开始按钮进入函数 `0x00405dde`，恢复出的顺序如下：

```text
关键词非空
  -> 地区非空
  -> 日期必须为 YYYY-MM-DD
  -> 搜索起始日 >= 2006-06-01
  -> 资讯起始日 >= 2017-07-03
  -> 结束日 <= 昨天
  -> 开始日 <= 结束日
  -> 统计关键词数、地区数和查询年数
  -> 用户二次确认
  -> 切换“开始/停止”状态
  -> 按任务循环请求
  -> 解密并刷新表格（可关闭刷新）
  -> 按选择的时间跨度聚合
  -> 按存放模式写 CSV
```

对应字面量证据：

| 行为 | PE VA / 函数 |
|---|---|
| “请添加关键词内容” | `0x005cdb93` → `fcn.00405dde` |
| “请选择要查询的地区” | `0x005cdba4` → `fcn.00405dde` |
| `2006-06-01` | `0x005cdbe7` → `fcn.00405dde` |
| `2017-07-03` | `0x005cdc22` → `fcn.00405dde` |
| “即将开始查询？”及关键词/地区计数 | `0x005cdead`..`0x005cdf09` |
| “状态：即将开始查询...” | `0x005cf674` → `fcn.0040df30` |
| “状态：开始运行...” | `0x005df3e4` → `fcn.00426b5e` / `0x004291c1` |

Python 映射：`src/baidu_index_tool/task_logic.py` 和 `qt_gui.MainWindow._start_query()`。

## 3. 搜索指数协议

现代请求构造器为 `fcn.0042573a`，在 `0x0042589e` 与 `0x00425a71` 引用模板 `VA 0x005df2db`：

```text
GET https://index.baidu.com/api/SearchApi/index
area=<AREA_ID>
word=[[{"name":"<KEYWORD>","wordType":1}]]
startDate=YYYY-MM-DD
endDate=YYYY-MM-DD
```

旧兼容构造器 `fcn.00407687` 引用 `VA 0x005ce030`；同一函数也引用资讯模板 `VA 0x005ce09f`：

```text
/api/FeedSearchApi/getFeedIndex
```

请求头字符串含 `Referer: https://index.baidu.com/v2/main/index.html`、`X-Requested-With: XMLHttpRequest`、Cookie 和浏览器 UA。Python 映射：`api.BaiduIndexClient` 与 `transport.UrlLibTransport`。

## 4. ptbk 解密

`fcn.0041934f` 在 `0x0041936b` 引用 `VA 0x005d1221`：

```text
https://index.baidu.com/Interface/api/ptbk?uniqid=<UNIQID>
```

恢复的算法：

1. 主响应提取 `uniqid`。
2. ptbk 返回字符串长度必须为偶数。
3. 前半段作为密文字母表，后半段作为明文字母表。
4. `dict(zip(first_half, second_half))` 建立逐字符替换表。
5. 解密 `_all`、`_pc`、`_wise` 数据，按逗号拆成数列，与 `startDate` 逐日对齐。

Python 映射：`crypto.build_translation_table()`、`crypto.decrypt_series()`、`api.parse_search_payload()`。

## 5. 状态分支

原文件在 `0x005d10f3`..`0x005d11af` 放置了完整 JSON 状态夹具：

- `10000`：未登录 / no login。
- `10001`：请求限制 / exceed speed limit。
- `10002`：bad request。
- `1 + no data`：当前条件无数据。

当前服务还会返回 `10018` 异常访问状态，重构把它与请求限制放到同一可重试分支。Python 映射：`api.BaiduIndexClient._check_status()`。

## 6. 地区、聚合与文件

- 原只读数据段中的省市串恢复出 393 个地区：全国 1、省级 34、城市 358。
- 搜索 CSV 字面量 `VA 0x005cddf6`：`关键词,日期,省份,城市,PC+移动端,PC端,移动端`。
- 资讯 CSV 字面量 `VA 0x005cddc7`：`关键词,日期,省份,城市,资讯指数`。
- 导出函数：`fcn.00439a03`；另一路写出点在 `fcn.00405dde`。
- 存放模式控件字面量：全部合并、每个关键词、关键词和地区分别存放。
- 时间跨度字面量：`周 (日均)`、`月 (日均)`、`年 (日均)`、`总均值(日均)`。

外层 `数据目录/` 的 55 个历史 CSV 进一步验证：UTF-8 BOM、中文表头、`YYYYMMDD-YYYYMMDD` 区间标签、中文时间戳目录和 `<关键词>-年（日均）-<起止日期>.csv` 命名。

Python 映射：`region_data.py`、`aggregate.py`、`export.py`、`task_logic.output_filename()`、`webview_gui.SystemWebViewApi.query()`。

## 7. 软件内登录

原程序登录地址字面量位于 `VA 0x005e1b5b`：

```text
https://index.baidu.com/v2/index.html#/
```

Miniblink Cookie API 表在 `0x0067ff8c` 附近。新版本以系统 WebView 等价重构：

- pywebview 在软件窗口中呈现登录页。
- 系统 WebView 使用应用私有持久化目录。
- pywebview `get_cookies()` 同步包含 HttpOnly 在内的 Cookie。
- 检测 `BDUSS` / `BDUSS_BFESS` 后更新主界面登录状态。
- 登录窗口由软件创建，不交给系统 Chrome。

Python 映射：`webview_gui.SystemWebViewApi.open_login()` 与 `sync_login_cookies()`。

## 8. 2026 当前网页签名差异

对当前网页包 `main-vendor.a580a6329788af13ac41.js` 的接口拦截器复核发现，
`trendIndex`、`trendFeedIndex`、`trendIndexLive` 在每次请求前都会调用
`window.Paris.getAcsInstance(...).getSign(...)`，并把结果写入 `Cipher-Text` 请求头。
旧 Python 请求与早期页面探针缺少该字段，因此服务端返回 `10018`；同一账号、
同一网络下补入系统 WebView 生成的签名后，SearchApi 在浏览器 XHR 与 Python HTTP
两条路径均返回 `status=0`。

Python 映射：`webview_gui.SystemWebViewApi._signature()`、`api.BaiduIndexClient._get()`。
对应的签名请求、页面会话和返回值处理已纳入自动化测试。

当前 FeedSearchApi 的主序列位于 `data.index[*].data`，关键词位于 `key`；
`api.parse_news_payload()` 同时兼容这一结构与旧版 `userIndexes` 结构。

## 9. 品牌指数

原二进制中的品牌模块函数位于 `fcn.0041d8cf`，结合当前网页包与登录态接口重放恢复出：

```text
POST /insight/word/sug
POST /insight/brand/queryBrandIndex
POST /insight/brand/queryBrandSearchIndex
POST /insight/brand/queryBrandFeedIndex
POST /insight/brand/queryBrandClickIndex
```

先把用户输入的品牌词解析为 `entityId`，再以
`entityId/regionId/startDate/endDate/stat` 为通用请求体；品牌搜索指数追加
`source: 0`。当前品牌 API 使用的地区 ID 与 SearchApi 历史 ID 不同，
`brand_regions.py` 保留了当前页面的省市映射并在请求前转换。

Python 映射：`api.resolve_brand()`、`api.query_brand_metric()`、
`qt_worker.QueryWorker` 的 `brand` 分支、`aggregate_brand_points()` 和
`write_brand_csv()`。品牌实体解析、四项指数请求与导出逻辑已纳入自动化测试。

## 10. 其他恢复出的模块

原二进制还含下列独立模块，其端点和 CSV 结构已记录，但不属于本次截图所示的搜索指数主页面：

| 模块 | 函数 / 端点证据 |
|---|---|
| 实时近 24 小时 | `fcn.0041ea6b`；`/api/LiveApi/getLive` |
| 需求图谱 | `fcn.004273fa`；`/api/WordGraph/multi` |
| 人群画像 | `fcn.0042a9b3`；`SearchApi/region`、`SocialApi/baseAttributes`、`SocialApi/interest` |

## 11. 验证覆盖

离线测试夹具覆盖请求参数、搜索/品牌地区 ID、ptbk 解密、空槽数值、错误状态、资讯/品牌解密、日均聚合、CSV BOM/往返、任务校验和原版命名规则。GUI 测试还覆盖软件内登录、请求间隔、搜索空槽和品牌四指数。
