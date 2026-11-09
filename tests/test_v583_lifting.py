import unittest
from test_v58_regression import IsolatedDbMixin,m
class TestLiftingViews(IsolatedDbMixin,unittest.TestCase):
    def test_chinese_four_view_controls_and_export(self):
        from fastapi.testclient import TestClient
        con=m.db();con.execute("INSERT INTO items(name,qty,length_mm,width_mm,height_mm,weight_kg,package_type,stackable,rotatable) VALUES('中文设备',1,1000,1000,800,1000,'裸装',0,1)");con.commit();con.close()
        client=TestClient(m.app);html=client.get('/').text
        for s in ['data-container-view="perspective">三维','data-container-view="top">俯视','data-container-view="front">侧视','data-container-view="side">端视','导出中文吊点图（SVG）','吊点未确认','/static/lifting.js?v=5.8.28']:
            self.assertIn(s,html)
        self.assertEqual(client.get('/static/lifting.js').status_code,200)
    def test_empty_data_page_renders(self):
        from fastapi.testclient import TestClient
        self.assertEqual(TestClient(m.app).get('/').status_code,200)
