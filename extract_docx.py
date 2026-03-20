import zipfile, xml.etree.ElementTree as ET, sys, os

def extract_docx_text(filepath):
    with zipfile.ZipFile(filepath, 'r') as z:
        content = z.read('word/document.xml')
    root = ET.fromstring(content)
    paragraphs = []
    for para in root.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p'):
        texts = [run.text for run in para.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t') if run.text]
        paragraphs.append(''.join(texts))
    return '\n'.join(paragraphs)

output = extract_docx_text(os.path.join(os.path.dirname(__file__), 'race_sim_spec.docx'))
out_path = os.path.join(os.path.dirname(__file__), 'race_sim_output.txt')
with open(out_path, 'w', encoding='utf-8') as f:
    f.write(output)
print('Done. Output written to', out_path)
