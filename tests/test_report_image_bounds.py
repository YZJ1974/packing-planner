import struct,zlib,tempfile,unittest
from pathlib import Path
from docx import Document
from docx.shared import Mm
from app.word_report import picture
class ReportImageBoundsTests(unittest.TestCase):
 def check_image(self,w,h):
  def chunk(tag,data):return struct.pack('!I',len(data))+tag+data+struct.pack('!I',zlib.crc32(tag+data)&0xffffffff)
  png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',w,h,8,2,0,0,0))+chunk(b'IDAT',zlib.compress((b'\0'+b'\xff'*(3*w))*h))+chunk(b'IEND',b'')
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)/'image.png';p.write_bytes(png);shape=picture(Document(),p,width=69,height=78);self.assertLessEqual(shape.width,Mm(69));self.assertLessEqual(shape.height,Mm(78)+1);self.assertAlmostEqual(shape.width/shape.height,w/h,places=5)
 def test_wide_end_view_fits_table_cell(self):self.check_image(320,220)
 def test_tall_end_view_keeps_height_limit(self):self.check_image(220,320)
