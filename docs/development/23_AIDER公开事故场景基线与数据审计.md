# AIDER 事故场景公开基线与数据审计

## 数据来源与权利边界

- 来源：[AIDER Zenodo record 3888300](https://zenodo.org/records/3888300)，压缩包 `AIDER.zip`。
- Zenodo API 文件大小：275,701,096 bytes；MD5：`1ad4eb02ed156e8dfa19986ff382e58b`；SHA-256：`5c5c1eb25653642da7ee79ef30dfd3eb82eccaeb1e7a5ee587b3582bf056ba00`。
- Zenodo 元数据标注 `CC-BY-4.0`，同时含第三方 IEEE 版权说明。源图、衍生权重和模型暂留本机研究目录；获得明确再分发/部署许可前，不上传 Git、服务器或公开下载。
- 数据只含五类航拍场景图像，没有航次、事故事件或拍摄场景 ID。任何由此得到的精度均为**图像级公开基线**，不能写成独立事故事件准确率，也不能代表武汉大学现场表现。

## 完整性与标签审计

压缩包校验通过，6433 张 JPEG 均已安全解压。类别目录数量与 [TakuNet 的 AIDER 数据读取器](https://raw.githubusercontent.com/DanielRossi1/TakuNet/main/src/datasets/aider.py)相符：

| AIDER 类别 | 原始数量 | 处理 |
|---|---:|---|
| `collapsed_building` | 511 | 保留 |
| `fire` | 521 | 保留 |
| `flooded_areas` | 526 | 保留 |
| `normal` | 4390 | 排除 2 张与事故类逐字节相同的冲突副本 |
| `traffic_incident` | 485 | 排除 2 张与正常类逐字节相同的冲突副本 |
| **合计** | **6433** | **排除冲突图 4 张，入模 6429 张** |

两个冲突组分别是一张相同图像同时标为 `normal` 和 `traffic_incident`。已检查文件 SHA-256；冲突副本不会进入训练、验证或测试。其余完全重复图像以哈希组为单位拆分，防止同一文件跨拆分。近似重复图和共同拍摄场景无法由数据元信息识别。

## 可复现清单

`scripts/vision/prepare_aider_manifest.py` 固定随机种子，使用 TakuNet `PROPORTIONAL_SPLITS` 每类数量作为拆分比例基准，并将同哈希图像放在同一子集。对冲突副本做审计排除后，当前实际拆分为：

| 子集 | 图片数 |
|---|---:|
| 训练 | 3788 |
| 验证 | 520 |
| 测试 | 2121 |

逐类样本数、图像 SHA-256、拆分组、排除记录及限制写入本机生成的 `aider-manifest.json`。默认输出目录应在仓库外。

```powershell
python scripts/vision/prepare_aider_manifest.py `
  --dataset-root D:\LuoJiaExperimentData\WHUWalker_vision_research\AIDER_extracted `
  --output D:\LuoJiaExperimentData\WHUWalker_vision_research\AIDER_artifacts\aider-manifest.json `
  --seed 20261010 --split-protocol takunet-proportional
```

## 模型基线与判定

`scripts/vision/train_aider.py` 使用 MobileNetV3-small ImageNet 初始化，输出顺序固定为：
`collapsed_building, fire, flooded_areas, normal, traffic_incident`。图像预处理复用项目推理入口的 OpenCV RGB 缩放及 ImageNet 归一化；导出 ONNX 后检查项目实际分类器预处理下的 PyTorch/ONNX 概率差异。训练、检查点和 ONNX 都留在仓库外的本地研究目录。

事故阈值只由验证集选择，测试集只做一次留出评估。测试报告需包含五类混淆矩阵、事故二分类 precision/recall、由图像哈希组重采样的区间和 ONNX 一致性。由于没有事故级分组，置信区间也只能解释为图像级。

方案预设门槛为候选事件 precision ≥ 0.90、recall ≥ 0.80。未同时达到时不得把分类结果用作正式事故事件，更不得自动封路。即使图像级达标，也仍需校园航拍样本、事件级标注、道路区域关联和权利审查后才能申请启用；AIDER 分类只提供画面级线索，不提供事故车辆精确定位。

## 当前训练记录

训练于 2026-10-10 在独立 Python 3.14.2、PyTorch 2.14.1 CPU 环境完成 12 轮，使用 torchvision MobileNetV3-small 的 ImageNet V1 初始化。最佳检查点为第 11 轮；训练约 746 秒。测试集没有参与选轮或阈值选择。

| 指标 | 结果 | 解释 |
|---|---:|---|
| 测试图像数 | 2121 | 哈希去重组拆分的静态图像级留出 |
| 五类场景准确率 | 0.955210 | AIDER 五类分类总体准确率 |
| 五类宏平均 F1 | 0.921572 | 对五类等权平均 |
| 测试事故画面 precision | 0.929134 | 使用验证集确定的阈值 0.87921935 |
| 测试事故画面 recall | 0.855072 | 同上；138 张正例中识别 118 张 |
| 哈希组 bootstrap 95% 区间 | precision [0.878788, 0.971014]；recall [0.792593, 0.907692] | 只表示图像级不确定性，不是事故事件区间 |
| 验证集阈值 | 0.87921935 | 30 张事故正例；precision 0.96、recall 0.80 |
| 项目 ONNX 预处理一致性 | 最大概率差 1.16×10⁻⁶ | 小于 1×10⁻⁴ 容差；独立 ONNX 评测与训练报告一致 |
| ONNX 单图 CPU 推理 | 约 0.003 秒/张 | 独立评测机本地测量，不代表服务器吞吐 |

测试点估计达到 0.90/0.80 预设值，但图像级 95% 区间下界没有达到这两个值；而且 AIDER 没有视频事件标签。**事故事件级 precision/recall 尚未验收，路况联动保持关闭。**必须取得带独立航次/场景 ID 的视频样本、管理员逐事件真值及相应用途授权后，才能验证连续帧候选、时空定位和路线影响。

最终报告、测试清单、ONNX 和检查点保存在仓库外的本机研究目录。报告记录的 ONNX SHA-256 为 `4fd61dff23fdaf3c44455e1b604393a4810e4540ba91a1fde6aa1b79f195bebe`；ImageNet V1 初始化权重 SHA-256 为 `047dcff4addef86ea5bc2eff13c9614dc11f47ab1160d0a71a25e7db994f4e1f`。没有源影像或模型权重进入仓库/线上服务。
