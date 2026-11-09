import copy
import unittest
from app.cargo_labels import annotate_cargo_names
from app.word_report import cargo_rows


class CargoNameTests(unittest.TestCase):
    def test_explicit_crate_links_keep_identity_and_report_actual_name(self):
        unit = dict(asu_key='crate:74', name='BX-SRC-033', type='木箱',
                    seq=59, l=1000, w=1000, h=2430, weight_kg=362)
        rec = {'plans': {'40HQ': {'containers': [{'placements': [unit]}]}}}
        before = copy.deepcopy(rec)
        annotated = annotate_cargo_names(rec,
            [dict(crate_id=74, name='低压柜1', qty=1)],
            [dict(id=74, name='BX-SRC-033')])
        box = annotated['plans']['40HQ']['containers'][0]
        q = box['placements'][0]
        self.assertEqual(q['name'], 'BX-SRC-033')
        self.assertEqual(q['cargo_name'], '低压柜1')
        self.assertEqual(q['display_name'], '低压柜1（箱号：BX-SRC-033）')
        self.assertEqual(cargo_rows(box)[0][1], '低压柜1\n箱号：BX-SRC-033')
        self.assertEqual(rec, before)

    def test_mixed_contents_retain_every_linked_name_and_quantity(self):
        rec = {'packages': [dict(asu_key='crate:2', name='BX-002')]}
        a = annotate_cargo_names(rec,
            [dict(crate_id=2, name='阀门', qty=3), dict(crate_id=2, name='管件', qty=5),
             dict(crate_id=3, name='其他设备', qty=1)],
            [dict(id=2, name='BX-002')])['packages'][0]
        self.assertEqual(a['cargo_name'], '阀门 ×3；管件 ×5')
        self.assertNotIn('其他设备', a['display_name'])

    def test_bare_units_and_unknown_links_are_not_matched_by_similar_names(self):
        rec = {'packages': [dict(asu_key='item:2', name='BX-002'),
                            dict(asu_key='crate:99', name='BX-002')]}
        result = annotate_cargo_names(rec, [dict(crate_id=2, name='阀门', qty=1)],
                                     [dict(id=2, name='BX-002')])
        self.assertEqual(result, rec)
