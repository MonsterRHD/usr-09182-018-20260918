# 创新项目里程碑拨付

这是一个面向真实业务协作的服务端项目。领域材料位于 `contracts/domain.json`，示例事件位于 `fixtures/events.json`。实现需要保持事件身份、发生时间和业务版本之间的可追溯关系，并为异常恢复、权限隔离和审计提供明确边界。

基线检查使用 Python 标准库运行：

```bash
python3 -m unittest discover -s tests -v
```
