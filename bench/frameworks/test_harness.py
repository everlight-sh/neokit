#!/usr/bin/env python3
"""Oracle rejection tests: semantic equality must detect changed output."""
import html,time,unittest
from model import rows,validate

class OracleTests(unittest.TestCase):
    def fixture(self,n=3,seed=17,token='<unsafe> & café',interactive=False):
        data=rows(n,seed);checksum=sum(int(r[2][:-3])+int(r[3]) for r in data)
        cells=''.join('<tr>'+''.join('<td>'+html.escape(c)+'</td>' for c in row)+'</tr>' for row in data)
        return (f'<main id="benchmark" data-n="{n}" data-seed="{seed}"><p id="request-token">{html.escape(token)}</p><output id="render-stamp">{int(time.time()*1000)}</output><tbody id="catalog-rows">{cells}</tbody><td id="checksum">{checksum}</td>'+('<section data-web-widget="counter"></section>' if interactive else '')+'</main>').encode()
    def check(self,body):return validate(body,3,17,'<unsafe> & café',False)
    def test_valid(self):self.check(self.fixture())
    def test_changed_row_rejected(self):
        with self.assertRaises(AssertionError):self.check(self.fixture().replace(b'Gadget Basic',b'Wrong item'))
    def test_missing_row_rejected(self):
        with self.assertRaises(AssertionError):self.check(self.fixture().replace(b'<tr>',b'<!--',1).replace(b'</tr>',b'-->',1))
    def test_unescaped_token_rejected(self):
        with self.assertRaises(AssertionError):self.check(self.fixture().replace(b'&lt;unsafe&gt;',b'<unsafe>'))
    def test_checksum_rejected(self):
        with self.assertRaises(AssertionError):self.check(self.fixture().replace(b'id="checksum">',b'id="checksum">1'))
    def test_missing_interactivity_rejected(self):
        with self.assertRaises(AssertionError):validate(self.fixture(),3,17,'<unsafe> & café',True)
    def test_stale_stamp_rejected(self):
        with self.assertRaises(AssertionError):self.check(self.fixture().replace(b'<output id="render-stamp">',b'<output id="render-stamp">0<!--').replace(b'</output>',b'--></output>'))
if __name__=='__main__':unittest.main()
