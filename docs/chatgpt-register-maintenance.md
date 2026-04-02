# OpenAI / Codex CLI 注册链路排查与修复方案

这份文档用于后续维护 `OpenAI / Codex CLI` 注册链路。

目标不是一次性“猜对”风控变化，而是把问题拆成几段，先定位坏在哪一段，再只修改那一段代码。

## 一、排查原则

1. 先做最小复现，不要一上来并发跑。
2. 先确认失败落点，不先猜原因。
3. 每次只改一个点，再重新跑单号验证。
4. 优先修状态识别，其次修动作，最后才修会话复用。
5. 邮箱和验证码问题单独处理，不要把主注册机一起改乱。

## 二、最小复现方式

建议固定以下条件：

- `count=1`
- `concurrency=1`
- 固定一个代理
- 固定一个邮箱渠道
- 不要同时开多个平台任务

这样可以先判断是链路变化，还是并发、代理池、邮箱池带来的噪声。

## 三、当前链路拆分

当前 `OpenAI / Codex CLI` 注册主链路主要分为 4 段：

1. 预授权与起始跳转
   文件：`platforms/chatgpt/chatgpt_client.py`
   关键函数：
   - `visit_homepage()`
   - `get_csrf_token()`
   - `signin()`
   - `authorize()`

2. 注册状态机推进
   文件：`platforms/chatgpt/chatgpt_client.py`
   关键函数：
   - `register_complete_flow()`
   - `_follow_flow_state()`
   - `register_user()`
   - `verify_email_otp()`
   - `create_account()`

3. 注册后会话复用与取 Token
   文件：`platforms/chatgpt/chatgpt_client.py`
   关键函数：
   - `reuse_session_and_get_tokens()`
   - `fetch_chatgpt_session()`
   - `get_next_auth_session_token()`

4. 邮箱生成与验证码接收
   文件：
   - `core/base_mailbox.py`
   - `core/outlook_register_mailbox.py`

## 四、先判断属于哪类问题

### 1. 预授权阶段失败

典型表现：

- 首页访问失败
- `csrf` 取不到
- `signin` 没返回 authorize URL
- `authorize` 跳到 `/error`
- 命中 Cloudflare / SPA 中间页

优先检查：

- `visit_homepage()`
- `get_csrf_token()`
- `signin()`
- `authorize()`

高概率原因：

- 首页结构变了
- 请求头要求变了
- Cloudflare 风控增强
- `auth.openai.com` 跳转参数变化

### 2. 状态识别错了

典型表现：

- 日志里出现“未知起始状态”
- 状态机卡住
- 明明页面已跳转，但代码还在按旧流程走

优先检查：

- `platforms/chatgpt/utils.py`
- `extract_flow_state()`
- `describe_flow_state()`
- `_state_is_password_registration()`
- `_state_is_email_otp()`
- `_state_is_about_you()`
- `_is_registration_complete_state()`

高概率原因：

- 路径变了
- `continue_url` 字段变了
- 返回 JSON 结构变了
- 新增了新的中间页类型

### 3. OTP 问题

典型表现：

- 邮件迟迟收不到
- 拿到了旧验证码
- 邮件已到，但代码没识别出 6 位码

优先检查：

- `core/base_mailbox.py`
- 对应 provider 的 `wait_for_code()`
- `exclude_codes`
- `otp_sent_at`

高概率原因：

- 邮件服务商变慢
- 邮件内容模板改了
- 邮箱返回顺序变了
- 正则太宽或太窄

### 4. 注册成功但拿不到 Session / Token

典型表现：

- `register_complete_flow()` 成功
- 但 `reuse_session_and_get_tokens()` 失败
- 没有 `__Secure-next-auth.session-token`
- `/api/auth/session` 不返回 `accessToken`

优先检查：

- `reuse_session_and_get_tokens()`
- `fetch_chatgpt_session()`
- `get_next_auth_session_token()`
- `_follow_flow_state()`

高概率原因：

- 回调没有真正落地
- cookie 域名或名字变了
- `/api/auth/session` 返回结构变了

## 五、推荐修改顺序

### 第一步：先修状态识别

如果页面路径、参数、返回结构变了，先修改：

- `platforms/chatgpt/utils.py`

不要一上来就去改注册动作，否则容易把原本没坏的步骤一起弄乱。

### 第二步：再修具体动作

如果是某个具体动作失败，再改对应函数：

- 密码页变了：改 `register_user()`
- 验证码页变了：改 `verify_email_otp()`
- about-you 页变了：改 `create_account()`

文件：

- `platforms/chatgpt/chatgpt_client.py`

### 第三步：最后修会话复用

如果注册本身已经走通，只是最后拿不到 token，再处理：

- `reuse_session_and_get_tokens()`
- `fetch_chatgpt_session()`

不要反过来先改 token 提取逻辑。

### 第四步：邮箱问题单独处理

邮箱生成、邮件到达、验证码提取异常，只改 mailbox provider：

- `core/base_mailbox.py`
- `core/outlook_register_mailbox.py`

不要把 OpenAI 主注册逻辑和邮箱逻辑混在一起改。

## 六、建议长期保留的排查信息

每次排查风控变化，至少要保留这些信息：

- 最后一个成功的 URL
- 第一个失败的 URL
- 最后一个识别出来的 `page_type`
- 关键接口状态码
- 是否拿到 `__Secure-next-auth.session-token`
- `/api/auth/session` 是否返回 `accessToken`
- 当次代理
- 当次邮箱渠道

如果两次运行结果不同，优先对比：

- 昨天成功日志
- 今天失败日志

不要只看源码猜。

## 七、调试模式使用建议

建议在项目里保留一个可开关的调试模式，默认关闭。

关闭时：

- 不额外写调试文件
- 不影响现有任务速度和日志量

开启时：

- 把关键 URL
- 状态机跳转
- 关键接口状态码
- 关键响应片段
- cookie 命中情况

写入本地调试文件，方便排查风控变化。

推荐用途：

1. OpenAI 风控疑似变化时先开调试模式跑单号
2. 找出失败落点后关闭调试模式再改代码
3. 改完后继续开调试模式跑一次确认链路恢复

## 八、建议新增的运维开关

除了调试模式，建议再保留一个和 `CPA` 相关的运维开关。

### 1. 是否开启定时清理 CPA

建议新增配置项：

- `cpa_cleanup_enabled`

建议默认值：

- `false`

原因：

- `CPA` 清理通常属于运维动作，不应该默认自动执行
- 如果规则写错，自动清理会把本来还能用的账号一起删掉
- 在没有充分观察规则前，默认关闭更安全

### 2. 定时清理间隔多久

建议新增配置项：

- `cpa_cleanup_interval_seconds`

建议默认值：

- `43200`

原因：

- 太短会增加误删风险
- 太长又会让无效号堆积
- `43200` 秒，也就是 `12` 小时，是比较保守、稳定的起点

### 3. 间隔建议

可以按场景使用：

- 小规模使用：`86400` 秒一次
- 常规使用：`43200` 秒一次
- 高量运行：`21600` 秒一次

不建议一开始就低于：

- `3600` 秒

因为这类清理逻辑本身通常不是链路核心，而是辅助运维动作，频率过高收益不大，风险反而更高。

### 4. 建议清理规则

定时清理不建议做“全量删除”，而是只处理明确无效的账号。

建议只清理：

- 已明确失效
- 已过期且刷新失败
- 多次检测失败
- 长时间未恢复的异常账号

不建议直接清理：

- 刚注册完成但还没稳定的账号
- 仅一次检测失败的账号
- 没有完成二次验证的账号

### 5. 推荐做法

推荐顺序：

1. 先上线 `cpa_cleanup_enabled`
2. 默认关闭
3. 先手动执行几轮验证规则
4. 确认误删风险可控后，再开启定时任务
5. 初始间隔先用 `43200` 秒，再根据数据调整

## 九、最常见的维护误区

### 误区 1：一失败就大改全链路

这会把真正的问题淹没掉。

正确做法：

- 先定位到具体一段
- 只改那一段

### 误区 2：邮箱问题和主注册问题一起改

这会让回归验证很难做。

正确做法：

- OpenAI 状态机问题改 `platforms/chatgpt`
- 收码问题改 `core/base_mailbox.py`

### 误区 3：并发跑出错就认为协议失效

并发问题经常是代理、邮箱、频控造成的，不一定是注册逻辑坏了。

正确做法：

- 先单号复现
- 再恢复并发

## 十、维护时优先查看的文件

协议与状态机：

- `platforms/chatgpt/chatgpt_client.py`
- `platforms/chatgpt/utils.py`
- `platforms/chatgpt/register_v2.py`

邮箱与验证码：

- `core/base_mailbox.py`
- `core/outlook_register_mailbox.py`

任务入口：

- `api/tasks.py`
- `platforms/chatgpt/plugin.py`

自动上传：

- `services/external_sync.py`
- `platforms/chatgpt/cpa_upload.py`

## 十一、建议的实际排查流程

1. 开启调试模式
2. 固定代理和邮箱，单号跑一次
3. 确认失败发生在预授权、状态识别、OTP、还是会话复用
4. 只改对应文件
5. 再跑单号验证
6. 成功后再恢复并发和代理池

## 十二、结论

后续只要链路变化，不要直接问“是不是整个注册机坏了”。

先回答这几个问题：

1. 坏在预授权、状态机、OTP、还是 token 复用？
2. 是页面状态识别错了，还是某个动作接口变了？
3. 是注册链路问题，还是邮箱/代理/风控问题？

按这个顺序排查，修复成本会小很多，也不容易把能跑的部分一起改坏。
