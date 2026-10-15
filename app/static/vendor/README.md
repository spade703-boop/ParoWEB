# 导出渲染依赖

`html2canvas-1.4.1.min.js` 来自 npm 官方 `html2canvas@1.4.1` 发行包，未修改，许可见 `html2canvas-LICENSE.txt`。仅在首次点击“个人总览导出”时加载。

发行包：https://registry.npmjs.org/html2canvas/-/html2canvas-1.4.1.tgz

已核对 npm 提供的 SHA-512：

```
fPU6BHNpsyIhr8yyMpTLLxAbkaK8ArIBcmZIRiBLiDhjeqvXolaEmDGmELFuX9I4xDcaKKcJl+TKZLqruBbmWA==
```

渲染库会在隔离的克隆文档中插入固定的伪元素隐藏样式。`app/main.py` 的 CSP 只允许该固定样式的 SHA-256（`UP0QZg7irvSMvOBz9mH2PIIE28+57UiavRfeVea0l3g=`），不开放 `unsafe-inline`。升级库时需重新验证此哈希及浏览器导出测试。
