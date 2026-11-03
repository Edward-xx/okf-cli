# Git 远端实现

本文说明当前实现的边界，替代早期设计稿。参数和错误码以 [CLI 参考](cli-contract.md) 为准，验证结果见 [实现状态](implementation-status.md)。

## 凭据与传输

凭据配置只保存 `env:VAR` 或 `file:PATH` 引用；Token 在执行时读取并注入 Git 子进程环境。子模块仅在 HTTPS 主机和端口与主仓库完全相同时继承 Token。SCP 地址保持原样，只有 `./`、`../` 开头的地址按主仓库解析。

主仓库 fetch 完成后尝试下载 LFS 和直接子模块对象；失败不撤销主仓库绑定。嵌套子模块不会递归下载，LFS 也不继承 Git HTTP Token。读取只使用本地缓存；LFS 内容返回前检查声明长度和 SHA-256。

## 预算

`--max-git-bytes` 采样检查 Git 与 LFS 对象存储占用。`--max-fetch-bytes` 比较主仓库 fetch 前后的 Git 对象清单，计量新增对象字节；检查发生在后续 LFS、子模块获取之前，不代表整个操作的网络流量。

## 显式 GC

`bundle cache NAME --gc` 与获取、刷新和压缩共用仓库锁：

1. 从当前绑定提交出发，递归扫描 gitlink，去重收集已下载的嵌套子模块提交。
2. 完整扫描后，为这些提交创建临时保护引用；对象缺失、扫描超限或保护失败时不执行回收。
3. 清理不再指向当前绑定提交的 `refs/okf/fetch-*`，执行 `git gc --prune=now`。
4. 删除本轮临时保护引用；下次 GC 重新扫描并建立保护。

Git 自身不会沿 gitlink 保留子模块对象，因此不能仅保护主仓库提交。GC 扫描最多检查 100000 个树条目，服从请求截止时间和取消信号。历史对象默认保留，只有显式 GC 才回收。

## 回归入口

- `cargo test --workspace`：固定提交读取、嵌套与共享子模块、连续 GC、依赖缺失保护，以及地址和凭据隔离单元测试。
- `python scripts/check_git_https.py PATH_TO_OKF`：本地 TLS 服务和真实 Git；覆盖认证、传输故障、预算、刷新和 GC 后子模块读取。
- `python scripts/check_git_integrity.py PATH_TO_OKF`：真实 CLI 的 LFS 缓存完整性与恢复测试。

测试仅使用临时仓库、测试 Token 和独立 `OKF_HOME`。HTTPS 脚本需要 `scripts/requirements-test.txt` 中的开发依赖；CLI 运行不依赖 Python。
