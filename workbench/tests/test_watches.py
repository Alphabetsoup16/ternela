# SPDX-License-Identifier: Apache-2.0
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from expansion_lab.storage import Store
from source_lab.evidence import shape, Examples, SourceIndex
from workbench.demo import catalog, snapshots
from workbench.engine import Watches, admit, observe, phase_plan


class WatchesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.templates={t['task']['id']:t for t in catalog()}
        cls.indexes={p:SourceIndex(p) for p in ['github','stripe','slack']}

    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        root=Path(self.temporary.name); self.now=1000.
        self.store=Store(root/'data')
        self.engine=Watches(root/'watches.sqlite3',self.store,lambda:self.now)
        self.template=self.templates['n02']; self.watch=self.create(self.template)

    def create(self, template):
        return self.engine.create(template['task'],template['plan'],template['phase'],30,self.indexes)

    def event(self, case='success', identity='event-1', revision=1, at=None, watch=None, template=None):
        template=template or self.template; watch=watch or self.watch
        return self.engine.submit(watch['id'],identity,revision,snapshots(template['task'],template['plan'],case,template['phase']),at)

    def test_complete_then_expired_then_reobserved(self):
        self.assertEqual(self.event()['receipt']['result']['status'],'complete')
        self.now+=30
        self.assertEqual(self.engine.get(self.watch['id'])['receipt']['result']['status'],'unknown')
        self.now+=1
        self.assertEqual(self.event(identity='event-2',revision=2)['receipt']['result']['status'],'complete')
        self.assertEqual(len(self.engine.outbox(self.watch['id'])),3)

    def test_duplicate_is_idempotent_even_if_retry_has_no_time(self):
        self.event(); self.now+=1; duplicate=self.event()
        self.assertTrue(duplicate['duplicate']); self.assertEqual(len(self.engine.outbox(self.watch['id'])),1)

    def test_reused_id_with_different_body_is_rejected(self):
        self.event()
        with self.assertRaisesRegex(ValueError,'different content'): self.event('processing')

    def test_expired_duplicate_cannot_return_a_current_success(self):
        self.event(); self.now+=31
        duplicate=self.event()
        self.assertEqual(duplicate['receipt']['result']['status'],'unknown')
        self.assertEqual(duplicate['delivery_receipt']['result']['status'],'complete')

    def test_duplicate_of_old_success_returns_newer_decision(self):
        self.event(); self.event('processing','new-event',2)
        duplicate=self.event()
        self.assertEqual(duplicate['receipt']['result']['status'],'pending')
        self.assertEqual(duplicate['delivery_receipt']['result']['status'],'complete')

    def test_old_revision_cannot_undo_complete(self):
        self.event(revision=3)
        old=self.event('processing','old',2)
        self.assertFalse(old['accepted']); self.assertEqual(self.engine.get(self.watch['id'])['receipt']['result']['status'],'complete')

    def test_new_revision_with_older_source_time_is_ignored(self):
        self.event(at=1000)
        self.assertFalse(self.event('processing','old',2,999)['accepted'])

    def test_restart_preserves_state_and_outbox(self):
        self.event(); restarted=Watches(self.engine.path,self.store,lambda:self.now)
        self.assertEqual(restarted.get(self.watch['id'])['revision'],1)
        self.assertEqual(len(restarted.outbox(self.watch['id'])),1)

    def test_acknowledgement_is_durable_and_idempotent(self):
        self.event(); identity=self.engine.outbox(self.watch['id'])[0]['id']
        self.engine.ack(self.watch['id'],identity); self.engine.ack(self.watch['id'],identity)
        self.assertEqual(self.engine.outbox(self.watch['id'])[0]['acked'],1000)

    def test_same_decision_refresh_does_not_emit_another_wakeup(self):
        self.event(); self.now+=5; self.event(identity='refresh',revision=2)
        self.assertEqual(len(self.engine.outbox(self.watch['id'])),1)
        self.assertEqual(self.engine.get(self.watch['id'])['receipt']['expires_at'],1035)

    def test_missing_snapshot_does_not_reuse_old_success(self):
        self.event()
        result=self.engine.submit(self.watch['id'],'missing',2,{})
        self.assertEqual(result['receipt']['result']['status'],'unknown')

    def test_permission_error_is_unknown(self):
        self.assertEqual(self.event('forbidden')['receipt']['result']['status'],'unknown')

    def test_wrong_amount_is_pending(self):
        self.assertEqual(self.event('wrong_amount')['receipt']['result']['status'],'pending')

    def test_nonfinite_and_future_time_rejected(self):
        for value in [float('nan'),float('inf'),1100,True]:
            with self.assertRaises(ValueError): self.event(at=value)

    def test_ttl_and_phase_validation(self):
        for ttl in [0,-1,float('nan'),True,100000]:
            with self.assertRaises(ValueError): self.engine.create(self.template['task'],self.template['plan'],'after',ttl,self.indexes)

    def test_outbox_cursor_and_recent_window_do_not_drop_new_events(self):
        for revision in range(1,111): self.event('success' if revision%2 else 'processing',str(revision),revision)
        first=self.engine.outbox(self.watch['id']); self.assertEqual(len(first),100)
        rest=self.engine.outbox(self.watch['id'],first[-1]['id']); self.assertEqual(len(rest),10)
        recent=self.engine.outbox(self.watch['id'],recent=True)
        self.assertEqual(recent[-1]['id'],rest[-1]['id']); self.assertEqual(recent[0]['id'],first[10]['id'])

    def test_unknown_schema_field_cannot_be_admitted(self):
        plan=deepcopy(self.template['plan']); plan['checks'][0]['field_pointer']='/made_up'
        with self.assertRaisesRegex(ValueError,'Undocumented'): admit(plan,self.template['task'],self.indexes)

    def test_json_plan_expressions_are_validated(self):
        plan=deepcopy(self.template['plan']); plan['checks'][0]['selector_json']='invalid'
        with self.assertRaises(ValueError): admit(plan,self.template['task'],self.indexes)

    def test_observable_requirement_needs_executable_checks(self):
        plan=deepcopy(self.template['plan']); plan['requirements'][0]['check_ids']=[]
        with self.assertRaisesRegex(ValueError,'executable'): admit(plan,self.template['task'],self.indexes)

    def test_quantity_errors_stop_dependent_preview(self):
        template=self.templates['n01']; watch=self.create(template)
        result=self.event('quantity_string',watch=watch,template=template)
        self.assertEqual(result['receipt']['result']['status'],'unknown')
        previews=[r['id'] for r in template['plan']['requests'] if r['operation']=='POST /v1/invoices/create_preview']
        for identity in previews: self.assertEqual(result['receipt']['observations'][identity]['trace'],[])

    def test_zero_quantity_preserved_without_boolean_coercion(self):
        template=self.templates['n01']; watch=self.create(template)
        receipt=self.event('quantity_0',watch=watch,template=template)['receipt']
        self.assertEqual(receipt['result']['status'],'ready')
        preview=next(v for v in receipt['observations'].values() if v['trace'] and v['trace'][0]['request']['operation']=='POST /v1/invoices/create_preview')
        quantity=preview['trace'][0]['request']['bindings']['body']['subscription_details']['items'][0]['quantity']
        self.assertIs(type(quantity),int); self.assertEqual(quantity,0)

    def test_before_does_not_fetch_after_only_observations(self):
        template=self.templates['n01']; selected=phase_plan(template['plan'],'before')
        self.assertEqual({c['phase'] for c in selected['checks']},{'before'})
        after={c['request_id'] for c in template['plan']['checks'] if c['phase']=='after'}
        before={r['id'] for r in selected['requests']}
        self.assertFalse(after & before)

    def test_example_shape_never_infers_required_or_enum(self):
        result=shape({'active':True,'count':0,'id':'U1'})
        self.assertNotIn('required',result); self.assertNotIn('enum',result['properties']['id'])
        self.assertEqual(result['properties']['count']['type'],'integer')

    def test_source_projection_does_not_modify_pinned_definition(self):
        index=self.indexes['slack']
        self.assertIsInstance(index.spec['definitions']['objs_user']['items'],list)
        self.assertTrue(index.supplements)


if __name__=='__main__': unittest.main()
