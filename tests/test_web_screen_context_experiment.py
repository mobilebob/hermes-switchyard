"""Offline contract regressions only; no claim of live semantic accuracy."""
import copy
import unittest

from hermes_switchyard import web_screening as w


class ContextContractTests(unittest.TestCase):
    def build(self,text='Literal public source content.'):
        source={'url':'https://example.org/manual','title':'Public manual','content':text}
        chunks,_=w.plan_chunks('web_extract',[source])
        return w.question_contract('What is the documented setting?',chunks)

    def test_contract_has_experimental_context_version(self):
        state,_=self.build()
        self.assertEqual(state['contract'],'web_screen.shadow.context.v2')

    def test_quoted_examples_are_explicitly_inert_not_keyword_hits(self):
        _,qs=self.build()
        self.assertIn('communicative purpose',qs['injection_0']['instructions'])
        self.assertIn('quoted attack examples',qs['injection_0']['instructions'])

    def test_fences_are_not_a_safety_whitelist(self):
        _,qs=self.build()
        self.assertIn('not a blanket exemption',qs['injection_0']['instructions'])
        self.assertIn('execute the quoted',qs['injection_0']['instructions'])

    def test_human_procedures_and_deprecated_settings_are_distinguished(self):
        _,qs=self.build()
        self.assertIn('human or documented software',qs['injection_0']['instructions'])
        self.assertIn('deprecated configuration',qs['injection_0']['instructions'])

    def test_mixed_and_disguised_attacks_remain_explicitly_positive(self):
        _,qs=self.build()
        for term in ('useful facts','polite','obfuscated','concealed'):
            self.assertIn(term,qs['injection_0']['instructions'])

    def test_independent_facts_are_not_discarded_for_quoted_examples(self):
        _,qs=self.build()
        self.assertIn('outside an inert example',qs['evidence']['instructions'])
        self.assertIn('not factual evidence',qs['evidence']['instructions'])

    def test_entire_quoted_source_retained_without_precleaning(self):
        text='Training quotation: "ignore previous instructions".\n```text\nexample\n```\nStable fact: 17.'
        state,qs=self.build(text)
        self.assertEqual(next(iter(state['chunks'].values()))['text'],'Public manual\n'+text)
        self.assertEqual(set(qs),{'relevance_0','injection_0','evidence'})
        self.assertEqual(qs['injection_0']['type'],'noul')
        self.assertEqual(set(qs['evidence']['criteria']),{'sufficient','insufficient','conflicting'})

    def test_labels_do_not_enter_contract(self):
        state,qs=self.build()
        before=copy.deepcopy((state,qs))
        gold={'injection':True,'relevant':False,'evidence':'insufficient'}
        self.assertTrue(gold)
        self.assertEqual((state,qs),before)
        self.assertEqual(set(state),{'contract','question','chunks'})


if __name__=='__main__':
    unittest.main()
