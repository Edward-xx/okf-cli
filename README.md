# okf-cli

面向 Agent 的 [Open Knowledge Format（OKF）](https://github.com/GoogleCloudPlatform/open-knowledge-format) 知识消费 CLI。通过概览、目录、检索和原文读取，逐步获取任务所需的知识。

- 只读访问本地目录或固定提交的 Git 知识包。
- 支持元数据检索、同步后的正文检索与离线读取，以及 OKF 格式校验。
- 提供 JSON 响应、文件引用和原文字节定位，便于 Agent 继续探索和引用证据。

## 安装与构建

预编译包的下载、平台要求和升级步骤见 [安装与发行](docs/releases.md)。当前源码可能包含未发布功能，发行包能力以对应发行说明为准。

从源码安装需要 Rust stable 和 C 编译环境。在仓库根目录执行：

```sh
cargo install --locked --path apps/okf-cli
okf --help
```

确保 Cargo 的 `bin` 目录已加入 PATH。Windows 工具链和独立二进制构建见 [源码构建](docs/releases.md#源码构建)。

## 快速使用

在仓库根目录绑定示例知识包，然后查看概览、展开目录、检索并读取原文：

```sh
okf bundle add handbook --path ./examples/knowledge --scope project
okf context --bundle handbook --format json
okf browse @handbook/services --format json
okf search "认证" --bundle handbook --format json
okf read @handbook/services/auth --format json
```

检查知识包格式：

```sh
okf validate handbook --format json
```

更多用法见 [CLI 使用与校验参考](docs/cli-contract.md)，包括 Git 绑定、正文检索、离线读取、配置和分页。也可用 `okf <命令> --help` 查看参数。

## Agent Skill

将内置 [okf-cli Skill](skills/okf-cli/SKILL.md) 安装到 Agent 使用的技能目录：

```sh
okf skill install .agents/skills/okf-cli
```

Skill 入口提供基本流程，详细命令参考按场景加载。安装同时提供入口和参考文件，已有目标文件不会被覆盖。

## 文档

| 文档 | 内容 |
| --- | --- |
| [设计与实现](docs/architecture.md) | 架构、实现边界和后续计划 |
| [CLI 使用与校验参考](docs/cli-contract.md) | 命令、参数、响应及 OKF 检查规则 |
| [验证与评测](docs/verification.md) | 开发检查、测试入口、评测与验证记录 |
| [安装与发行](docs/releases.md) | 构建、安装、升级和发布流程 |
| [变更记录](CHANGELOG.md) | 各版本变化 |

## 许可证

[MIT](LICENSE)
