from __future__ import annotations

import json
import zipfile
from pathlib import Path
from xml.etree import ElementTree


def extract_docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        xml = zf.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    return "\n".join(node.text or "" for node in root.findall(".//w:t", ns))


def main() -> None:
    docx = Path("链博会-班组长版.docx")
    text = extract_docx_text(docx)
    flow = [
        "1. 检查线路及接头是否保证机械强度和绝缘强度。docx 扣分输出：线路及接头不能保证机械强度和绝缘强度，扣 5～10 分。",
        "2. 检查线路是否设置短路、过载保护。docx 扣分输出：线路未设短路、过载保护，扣 5～10 分。",
        "3. 检查线路截面是否满足负荷电流。docx 扣分输出：线路截面不能满足负荷电流，每处扣 2 分。",
        "4. 检查线路设施、材料、相序排列、档距以及与邻近线路或固定物的距离是否符合规范要求。docx 扣分输出：线路设施、材料、相序排列、档距、与邻近线路或固定物的距离不符合规范要求，扣 5～10 分。",
        "5. 检查电缆是否沿地面明设，或沿脚手架、树木等不规范敷设。docx 扣分输出：电缆沿地面明设或沿脚手架、树木等敷设或敷设不符合规范要求，扣 5～10 分。",
        "6. 检查是否使用符合规范要求的电缆线路。docx 扣分输出：未使用符合规范要求的电缆线路，扣 10 分。",
        "7. 检查室内非埋地明敷主干线距地面高度是否不小于 2.5 米。docx 扣分输出：室内非埋地明敷主干线距地面高度小于 2.5m，每处扣 2 分。",
    ]
    output = [{
        "id": "level_3_distribution_box_line",
        "name": "三级配电箱线路检查",
        "enabled": True,
        "keywords": ["三级配电箱", "配电箱线路", "线路检查", "检查流程", "告诉我流程"],
        "flow": flow,
        "tts_reply": "三级配电箱线路检查流程有七项内容，请按照流程检查，并拍摄现场图像上传。",
        "source_excerpt": text[:200],
    }]
    target = Path("app/knowledge/check_flows.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已生成 {target}")


if __name__ == "__main__":
    main()
