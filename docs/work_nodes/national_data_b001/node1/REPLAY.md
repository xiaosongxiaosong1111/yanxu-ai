# B001 原件与固定版本回放

先在**新建的短路径克隆**中操作，避免写入正在运行的个人项目。先读取[本轮报告](REPORT.md)，安装`requirements.txt`并按[运行说明](../../../RUNNING.md)配置Xpdf4.00和独立Poppler26.07.0。不要复制用户的旧`runtime/`、Key或本地模型。

## 1. 恢复正式公共包

```powershell
python tools/prepare_data.py
```

该命令离线校验`data/bundles/manifest.json`，不创建SQLite、不调用AI或元宝。

## 2. 可选：恢复本批审核输入

只有继续处理未采用原件、审校或复盘时需要本节。此ZIP与正式采用包分开；它包含部分方向候选及明确失败，不应直接复制成`data/admissions.json`。

下面的PowerShell命令校验ZIP、白名单路径、每份文件SHA，拒绝覆盖不同原件。正式目录库和个人设置不在允许写入路径中：

```powershell
@'
import hashlib, json, zipfile
from pathlib import Path
root = Path.cwd().resolve()
folder = root / 'docs/work_nodes/national_data_b001/node1'
meta = json.loads((folder/'source-review-manifest.json').read_text(encoding='utf-8'))
archive = folder/meta['archive']
assert hashlib.sha256(archive.read_bytes()).hexdigest() == meta['archive_sha256']
prefixes = ('data/snapshots/', 'data/batch_runs/b001/',
            'data/runtime/national_catalog_b001/final-v4/')
allowed = [root/'data/snapshots', root/'data/batch_runs/b001',
           root/'data/runtime/national_catalog_b001/final-v4']
with zipfile.ZipFile(archive) as source:
    for item in meta['files']:
        relative = item['path']
        assert relative.startswith(prefixes) and '..' not in Path(relative).parts
        path = (root/relative).resolve()
        assert path.is_relative_to(root) and any(path.is_relative_to(base) for base in allowed)
        raw = source.read(relative)
        assert len(raw) == item['bytes']
        assert hashlib.sha256(raw).hexdigest() == item['sha256']
        if path.exists():
            assert path.read_bytes() == raw, 'Refuse to replace different bytes: '+relative
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
print('B001 public review inputs verified and restored')
'@ | python -X utf8 -
```

`final-v4/catalog-<unit_id>.json`是本批同一源码、同一输入的10校报告。更早的中间ECUST集合已经失效，不可沿用为根审核对象。final-v4报告属于首次固定版本；后续Accept改变了完整依赖图但未改变两份来源的165/109条事实，正式包已按最终代码重新批准并全量冷验，不能直接沿用旧图摘要。ZIP没有保存巨型候选admissions，正式恢复使用公共包；BNU462为证据刷新，ECUST274才是净增。

## 3. 离线重新解析单校

```powershell
$repoRoot = (Get-Location).Path
python -X utf8 -m collectors.national_catalog `
  --root $repoRoot --units "$repoRoot/data/units.json" `
  --sources "$repoRoot/data/batch_runs/b001/national-sources.json" `
  --unit-id 368142 --max-files 100 --refresh `
  --output "$repoRoot/data/batch_runs/b001/replay-368142.json" `
  --cache-dir "$repoRoot/data/runtime/b001-replay/368142"
```

该入口只解析保存的原件；`ready_entry_path`仍须经过增量采用、根PDF语义审核和显式发布。不能将`ready`、`collected`或`expected_records`当作全校目录完整。换成B001其他真实ID时保持相同约束。

## 4. 测试与发布

```powershell
python -X utf8 -m unittest discover -s tests
node --test tests/*.test.mjs
python -X utf8 -m collectors.publish_catalog --data-dir data
```

数据任务不准备或启动向量模型；隔离环境中5项本地BGE输入测试可能因缺模型失败，应与[已记录基线](verification.json)对照，不能改测试、伪造模型或将整库报告为全绿。本轮固定版本实际为2,531项、2,515通过、11跳过、5项同基线环境失败；12个重点模块综合覆盖均超过80%；金标新增校验路径语句/分支100%，完整数据见verification.json。

PDF审核会检查真实原件SHA、适配器依赖摘要与**完整当前记录集**摘要。修改v3或任一依赖后旧批准会失效；不要改审核摘要迎合新代码。五个冻结适配器连换行都不可改变。更换机器先核对工具版本、Git字节与审核图，不把安装任意同名PDF工具当作已复现。
