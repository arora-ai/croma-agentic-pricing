"""Render a .docx to PNG pages through Word (for visual checks). Usage: python docx_to_png.py file.docx out_dir"""
import subprocess
import sys
from pathlib import Path

src = Path(sys.argv[1]).resolve()
out = Path(sys.argv[2]).resolve()
out.mkdir(parents=True, exist_ok=True)
pdf = out / (src.stem + ".pdf")
ps = f"""$w = New-Object -ComObject Word.Application; $w.Visible = $false
$d = $w.Documents.Open('{src}'); $d.SaveAs2('{pdf}', 17); $d.Close(); $w.Quit()"""
subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True, timeout=180)
import fitz  # pymupdf

doc = fitz.open(pdf)
for i, page in enumerate(doc):
    page.get_pixmap(dpi=70).save(out / f"{src.stem}_p{i + 1}.png")
print(len(doc), "pages")
