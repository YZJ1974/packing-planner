# Packing Planner 装箱规划器

中文集装箱与货车配载工具，公开源码用于复现问题、审查计算和提交修复。当前程序基线 **V5.8.28**。

支持 Excel / CSV 材料导入、木箱分箱、集装箱与货车选择、自动排布、圆柱与袋装示意、三维/俯视/侧视/端视预览，以及 Word 四视图排布报告。计算采用启发式搜索，不能保证最少柜数或全局最优。

**欢迎纠错：** [提交问题](https://github.com/YZJ1974/packing-planner/issues/new/choose) · [贡献说明](CONTRIBUTING.md) · [已知限制](docs/LIMITATIONS.md)

## 快速启动

需要 Python 3.11 或更高版本。建议在本机运行，默认只监听 `127.0.0.1`；本项目没有配置面向公网的账号、访问控制和部署防护。

```bash
git clone https://github.com/YZJ1974/packing-planner.git
cd packing-planner
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Windows 将激活命令替换为 `.venv\Scripts\activate`。macOS 也可以双击 `启动装箱规划器.command`。

打开 <http://127.0.0.1:8000/>。首次启动会创建本地 `data/`；仓库不包含任何生产数据库、真实项目箱单、现场凭证或历史装箱结果。示例不会自动导入。

## 试用示例

在上传区选择 `examples/materials_demo.csv`；若要检查“桶/套/立方米”语义，选择 `examples/shipment_units_demo.csv`。两份文件全是虚构数据，仅用于复现。

新项目使用“仅使用本次清单”，替换前会备份当前材料和木箱。“追加”会保留旧货物，不适合作为新项目替换。页面会显示来源及文件指纹。

## Word 排布导出

Word 导出还需要 Node.js 和 `sharp`：

```bash
npm install
```

`package.json` 固定 `sharp` 的版本。程序优先使用明确配置的运行环境，也支持已存在的本机文档运行时；环境未就绪会提示配置，基本导入与规划不依赖此功能。

可通过环境变量指定运行路径：`PACKING_REPORT_PYTHON`、`PACKING_REPORT_NODE`、`PACKING_REPORT_NODE_MODULES`。最后一个变量指向包含 `sharp` 的 `node_modules` 目录。中文图片还需系统中有中文字体，例如 Noto Sans CJK SC、Microsoft YaHei 或 PingFang SC。

“装箱排布导出（Word）”输出所选方案全部柜的四视图、清单及方案外货物，图片不包含吊点与吊索。

## 测试

```bash
python -m unittest discover -s tests -v
```

公开发布副本在本地重新运行测试；GitHub Actions 会对后续 Push / Pull Request 运行相同测试。测试通过不等于工程验收或发运放行。

## 重点审查方向

- 自动导入与手动列映射的单位、数量、重量和运输资格是否一致。
- 替换导入、追加导入、旧列映射表单和已保存手调布局是否处理正确。
- 圆柱卧放/立放是否保留外形尺寸；预览是否与计算包络对应。
- 袋装数量、声明体积、重量是否闭合，装袋路径与顶部许可是否合理。
- 净空、碰撞、支撑、载荷、重心及导出门禁是否可复现。

请使用脱敏的最小输入报告问题。不要在 Issue 附上真实客户箱单、合同、凭证、数据库或本地账号信息。

## 工程使用边界

袋装采用声明体积分配，袋形只是示意，不是逐袋几何定位；顶部许可是项目输入，不能代替厂家承载数据。设备形心、支座、局部压力、绑扎、吊装及装卸工法存在独立的复核要求。详见 [已知限制](docs/LIMITATIONS.md)。

## 发布副本说明

生产计算代码保留 V5.8.28，公开副本去除一处项目特定界面文案并清理模板作者元数据，修正一处图形说明文字的 Python 3.11 兼容写法，补充了说明、虚构样例和贡献流程。本机工作目录及数据不受此仓库影响。

本仓库目前未指定开源许可证；公开可见不表示已授予通用的商业再分发许可。第三方依赖遵循各自许可证，见 [第三方说明](THIRD_PARTY.md)。
