import importlib.util
from pathlib import Path
import unittest
spec=importlib.util.spec_from_file_location('importer',Path(__file__).resolve().parents[1]/'scripts/oberstaufen.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class ImportTests(unittest.TestCase):
    def test_protected_and_unknown_are_excluded(self):
        d={'clientid':32,'clientname':'Markt Oberstaufen','parts':[{'protectedpart':True,'agendaitems':[{'restricted':False}]},{'protectedpart':False,'agendaitems':[{'restricted':False,'id':'public'},{'restricted':True},{'id':'unknown'}]}]}
        self.assertEqual(m.public_items(d),[{'restricted':False,'id':'public'}])
    def test_wrong_municipality_fails(self):
        with self.assertRaises(ValueError):m.public_items({'clientid':33,'clientname':'Other'})
    def test_nested_pdf_label(self):
        p=m.ReportLinks();p.feed('<a href="/fileadmin/report.pdf"><span>Bericht &amp; Anlage</span></a>');self.assertEqual(p.links[0]['titel'],'Bericht & Anlage')
