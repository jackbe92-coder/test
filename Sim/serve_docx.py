import zipfile
import xml.etree.ElementTree as ET
import http.server
import socketserver
import threading

def extract_docx_text(filepath):
    with zipfile.ZipFile(filepath, 'r') as z:
        content = z.read('word/document.xml')
    root = ET.fromstring(content)
    paragraphs = []
    for para in root.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p'):
        texts = [run.text for run in para.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t') if run.text]
        paragraphs.append(''.join(texts))
    return '\n'.join(paragraphs)

text = extract_docx_text(r'C:\Users\evil_\Desktop\Scraper\race_sim_spec.docx')

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(text.encode('utf-8'))
    def log_message(self, format, *args):
        pass

PORT = 19999
with socketserver.TCPServer(("", PORT), Handler) as httpd:
    print(f"Serving on port {PORT}")
    httpd.serve_forever()
