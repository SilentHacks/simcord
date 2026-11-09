"""Offline capture-integrity checks; no Discord SDK or credentials required."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "reference_validator",
    Path(__file__).resolve().parents[2] / "scripts" / "validate_monetization_reference.py",
)
validator = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(validator)


class Bundle:
    def __init__(self, directory, version=2):
        self.directory = directory
        self.version = version
        self.rows = {}
        self.manifest = {
            "schema_version": version,
            "application_id": "100",
            "user_id": "200",
            "guild_id": "300",
            "status": "completed",
            "cases": {},
            "owned_entitlements": {},
            "cleanup_errors": [],
            "pending_write": None,
        }
        self.request("GET", "/oauth2/applications/@me", {"id": "100"})

    def record(self, stream, **values):
        rows = self.rows.setdefault(stream, [])
        rows.append({"utc": "2026-10-21T00:00:00+00:00", "action": "measurement", **values})
        return f"{stream}.jsonl:{len(rows)}"

    def request(self, method, path, response=None, status=200, body=None, query=None):
        number = len(self.rows.get("requests", [])) + 1
        correlation = {"request_id": f"requests.jsonl:{number}"} if self.version == 2 else {}
        self.record("requests", method=method, path=path, body=body, query=query, attempt=1, **correlation)
        from urllib.parse import urlencode

        path_query = "/api/v10" + path + (("?" + urlencode(query)) if query else "")
        return self.record(
            "rest",
            method=method,
            path_query=path_query,
            body=body,
            status=status,
            response=response,
            attempt=1,
            **correlation,
        )

    def save(self):
        (self.directory / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")
        for stream, rows in self.rows.items():
            (self.directory / f"{stream}.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
            )
        return validator.validate_bundle(self.directory)


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bundle = Bundle(Path(self.temp.name))

    def codes(self, report):
        return {item["code"] for item in report["errors"]}

    def test_rejection_is_evidence_and_blocker_is_not_bad_capture(self):
        ref = self.bundle.request(
            "POST",
            "/applications/100/entitlements",
            {"code": 12345},
            403,
            {"sku_id": "400", "owner_id": "200", "owner_type": 2},
        )
        self.bundle.manifest["cases"] = {
            "rejected": {"status": "observed", "rest": [ref]},
            "expiry": {"status": "blocked", "reason": "Legitimate live fixture unavailable"},
        }
        report = self.bundle.save()
        self.assertTrue(report["valid"], report)
        self.assertEqual(len(report["pending_prerequisites"]), 1)
        self.assertEqual(report["coverage"]["rest_outcomes"], 2)

    def test_legacy_partial_evidence_needs_no_rerun(self):
        self.bundle = Bundle(Path(self.temp.name), version=1)
        report = self.bundle.save()
        self.assertTrue(report["valid"], report)
        self.assertTrue(report["legacy"])
        self.assertFalse(report["coverage"]["pagination"]["calibrated"])

    def test_truncation_and_malformed_json(self):
        self.bundle.save()
        path = self.bundle.directory / "rest.jsonl"
        path.write_text('{"utc":', encoding="utf-8")
        report = validator.validate_bundle(self.bundle.directory)
        self.assertIn("truncated_jsonl", self.codes(report))
        self.assertIn("invalid_jsonl", self.codes(report))

    def test_pending_and_missing_write_response(self):
        self.bundle.manifest["pending_write"] = {"method": "POST"}
        self.bundle.record(
            "requests", method="POST", path="/applications/100/entitlements", request_id="lost", body={}
        )
        self.assertIn("unresolved_mutation", self.codes(self.bundle.save()))

    def test_unknown_write_outcome(self):
        self.bundle.request("POST", "/applications/100/entitlements", status=None)
        self.bundle.rows["rest"][-1]["outcome_unknown"] = True
        self.assertIn("unresolved_mutation", self.codes(self.bundle.save()))

    def test_unowned_cleanup_is_rejected_even_if_delete_rejected(self):
        self.bundle.request("DELETE", "/applications/100/entitlements/999", {"code": 10029}, 404)
        self.bundle.rows["requests"][-1]["action"] = "cleanup"
        self.assertIn("unowned_cleanup", self.codes(self.bundle.save()))

    def test_owned_cleanup_requires_matching_owner_and_receipt(self):
        payload = {"id": "500", "application_id": "100", "sku_id": "400", "user_id": "200", "type": 8}
        body = {"sku_id": "400", "owner_id": "200", "owner_type": 2}
        self.bundle.request("POST", "/applications/100/entitlements", payload, body=body)
        self.bundle.request("DELETE", "/applications/100/entitlements/500", status=204)
        self.bundle.manifest["owned_entitlements"]["500"] = {**body, "deleted": True}
        self.assertTrue(self.bundle.save()["valid"])
        payload["user_id"] = "201"
        self.assertIn("invalid_create_provenance", self.codes(self.bundle.save()))

    def test_preexisting_id_never_becomes_owned(self):
        row = {"id": "500", "application_id": "100", "sku_id": "400", "user_id": "200", "type": 8}
        self.bundle.request("GET", "/applications/100/entitlements", [row])
        self.bundle.request(
            "POST",
            "/applications/100/entitlements",
            row,
            body={"sku_id": "400", "owner_id": "200", "owner_type": 2},
        )
        self.assertIn("invalid_create_provenance", self.codes(self.bundle.save()))

    def test_invalid_observation_and_fabricated_reference(self):
        self.bundle.manifest["observation_seconds"] = 60
        start = self.bundle.record("health", event="observation_start", connected=True)
        end = self.bundle.record(
            "health", event="observation_end", connected=True, utc="2026-10-21T00:00:01+00:00"
        )
        self.bundle.manifest["cases"]["absence"] = {"status": "observed", "health": [start, end]}
        self.bundle.manifest["cases"]["invented"] = {"status": "observed", "gateway": ["gateway.jsonl:99"]}
        codes = self.codes(self.bundle.save())
        self.assertIn("invalid_observation_window", codes)
        self.assertIn("broken_reference", codes)

    def test_disconnect_invalidates_window_but_not_cleanup_shutdown(self):
        self.bundle.record("health", event="observation_start", connected=True, requested_seconds=60)
        self.bundle.record("health", event="disconnected", utc="2026-10-21T00:00:30+00:00")
        self.bundle.record("health", event="observation_end", connected=True, utc="2026-10-21T00:01:00+00:00")
        self.assertIn("invalid_observation_window", self.codes(self.bundle.save()))

    def test_labels_do_not_prove_pagination_or_observation(self):
        self.bundle.manifest["cases"]["made_up"] = {"status": "observed"}
        self.bundle.manifest["pagination_calibrated"] = True
        codes = self.codes(self.bundle.save())
        self.assertIn("unsupported_observed_claim", codes)
        self.assertIn("unsupported_pagination_claim", codes)

    def test_full_page_is_partial_not_calibrated(self):
        self.bundle.request(
            "GET",
            "/applications/100/entitlements",
            [{"id": str(i)} for i in range(100)],
            query={"limit": "100"},
        )
        report = self.bundle.save()
        self.assertTrue(report["valid"], report)
        self.assertEqual(report["coverage"]["pagination"]["full_pages"], 1)
        self.assertFalse(report["coverage"]["pagination"]["calibrated"])

    def test_no_secret_echo_and_malformed_shapes_return_report(self):
        secret = "do-not-print-this-credential"
        self.bundle.manifest["token"] = secret
        report = self.bundle.save()
        self.assertIn("unredacted_secret", self.codes(report))
        self.assertNotIn(secret, json.dumps(report))
        self.bundle.manifest.pop("token")
        self.bundle.manifest["owned_entitlements"] = [None]
        self.assertFalse(self.bundle.save()["valid"])

    def test_correlation_query_and_application_identity(self):
        self.bundle.request("GET", "/applications/100/entitlements", [], query={"exclude_deleted": "false"})
        self.bundle.rows["rest"][-1]["path_query"] = (
            "/api/v10/applications/999/entitlements?exclude_deleted=true"
        )
        codes = self.codes(self.bundle.save())
        self.assertIn("correlation_mismatch", codes)
        self.assertIn("query_mismatch", codes)

    def test_authorized_source_purchase_rejection_is_not_cleanup(self):
        row = {"id": "500", "application_id": "100", "sku_id": "400", "user_id": "200", "type": 4}
        snapshot = self.bundle.request("GET", "/applications/100/entitlements/500", row)
        rejected = self.bundle.request("DELETE", "/applications/100/entitlements/500", {"code": 123}, 400)
        self.bundle.manifest["source_bundles"] = ["prior-capture"]
        self.bundle.manifest["cases"]["reject.non_sandbox_delete"] = {
            "status": "observed",
            "explicit_source_purchase": True,
            "source_bundle": "prior-capture",
            "rest": [snapshot, rejected],
        }
        self.assertTrue(self.bundle.save()["valid"])
        self.bundle.manifest["cases"]["reject.non_sandbox_delete"]["explicit_source_purchase"] = False
        self.assertIn("unowned_cleanup", self.codes(self.bundle.save()))

    def test_matching_response_does_not_authorize_third_party_owner(self):
        self.bundle.request(
            "POST",
            "/applications/100/entitlements",
            {"id": "500", "application_id": "100", "sku_id": "400", "user_id": "999", "type": 8},
            body={"sku_id": "400", "owner_id": "999", "owner_type": 2},
        )
        self.assertIn("invalid_create_provenance", self.codes(self.bundle.save()))

    def test_sequence_counter_change_invalidates_observation(self):
        self.bundle.record(
            "health",
            event="observation_start",
            connected=True,
            health_errors=0,
            connection_epoch=1,
            requested_seconds=60,
            monotonic_ns=1_000_000_000,
        )
        self.bundle.record(
            "health",
            event="observation_end",
            connected=True,
            health_errors=1,
            connection_epoch=1,
            utc="2026-10-21T00:01:00+00:00",
            monotonic_ns=61_000_000_000,
        )
        self.assertIn("invalid_observation_window", self.codes(self.bundle.save()))

    def test_duplicate_json_keys_cannot_override_evidence(self):
        self.bundle.save()
        (self.bundle.directory / "rest.jsonl").write_text('{"status":200,"status":204}\\n', encoding="utf-8")
        self.assertIn("invalid_jsonl", self.codes(validator.validate_bundle(self.bundle.directory)))

    def test_unknown_delete_probe_needs_a_prior_unknown_get_and_cannot_be_cleanup(self):
        self.bundle.request("GET", "/applications/100/entitlements/1", {"code": 10029}, 404)
        self.bundle.request("DELETE", "/applications/100/entitlements/1", {"code": 10029}, 404)
        self.bundle.rows["requests"][-1]["action"] = "reject.unknown_entitlement.delete"
        self.assertTrue(self.bundle.save()["valid"])
        self.bundle.rows["requests"][-1]["action"] = "cleanup"
        self.assertIn("unowned_cleanup", self.codes(self.bundle.save()))

    def test_consumption_never_adopts_a_live_purchase(self):
        row = {"id": "500", "application_id": "100", "sku_id": "400", "user_id": "200", "type": 1}
        self.bundle.manifest["purchase_entitlement_ids"] = ["500"]
        self.bundle.request("GET", "/applications/100/entitlements/500", row)
        self.bundle.request("POST", "/applications/100/entitlements/500/consume", status=204)
        self.assertIn("unverified_consume_target", self.codes(self.bundle.save()))

    def test_recovered_window_does_not_relabel_an_unfinished_old_window_as_observed(self):
        old = self.bundle.record("health", event="observation_start", connected=True, requested_seconds=1)
        self.bundle.record("health", event="disconnected", utc="2026-10-21T00:00:01+00:00")
        start = self.bundle.record(
            "health",
            event="observation_start",
            connected=True,
            requested_seconds=1,
            utc="2026-10-21T00:00:02+00:00",
        )
        end = self.bundle.record(
            "health",
            event="observation_end",
            connected=True,
            requested_seconds=1,
            utc="2026-10-21T00:00:03+00:00",
        )
        self.bundle.manifest["cases"]["recovered"] = {"status": "observed", "health": [start, end]}
        self.assertTrue(self.bundle.save()["valid"])
        self.bundle.manifest["cases"]["old"] = {"status": "observed", "health": [old]}
        self.assertIn("unsupported_observation_claim", self.codes(self.bundle.save()))

    def test_safe_read_recovery_keeps_the_lost_attempt_without_invalidating_fresh_evidence(self):
        self.bundle.record(
            "requests",
            method="GET",
            path="/applications/100/entitlements",
            request_id="lost-read",
            body=None,
            query=None,
            attempt=1,
        )
        ref = self.bundle.request("GET", "/applications/100/entitlements", [])
        self.bundle.manifest["cases"]["recovered"] = {"status": "observed", "rest": [ref]}
        report = self.bundle.save()
        self.assertTrue(report["valid"], report)
        self.assertIn("missing_response", {item["code"] for item in report["warnings"]})

    def test_two_guild_commands_require_same_guild_create_and_cleanup(self):
        self.bundle.manifest["other_guild_id"] = "301"
        commands = self.bundle.manifest["temporary_commands"] = {}
        for guild, command in (("300", "600"), ("301", "601")):
            ref = self.bundle.request("POST", f"/applications/100/guilds/{guild}/commands", {"id": command})
            self.bundle.request("DELETE", f"/applications/100/guilds/{guild}/commands/{command}", status=204)
            commands[guild] = {"id": command, "create_ref": ref, "deleted": True}
        self.assertTrue(self.bundle.save()["valid"])
        self.bundle.rows["requests"][-1]["path"] = "/applications/100/guilds/300/commands/601"
        self.bundle.rows["rest"][-1]["path_query"] = "/api/v10/applications/100/guilds/300/commands/601"
        codes = self.codes(self.bundle.save())
        self.assertIn("unowned_command_cleanup", codes)
        self.assertIn("temporary_command_not_cleaned", codes)

    def test_command_registry_requires_actual_creation_reference(self):
        ref = self.bundle.request("POST", "/applications/100/guilds/300/commands", {"id": "600"})
        self.bundle.request("DELETE", "/applications/100/guilds/300/commands/600", status=204)
        self.bundle.manifest["temporary_commands"] = {
            "300": {"id": "600", "create_ref": ref, "deleted": True}
        }
        self.assertTrue(self.bundle.save()["valid"])
        self.bundle.manifest["temporary_commands"]["300"]["create_ref"] = "rest.jsonl:1"
        self.assertIn("unsupported_command_cleanup", self.codes(self.bundle.save()))

    def test_resumed_command_registry_need_not_retain_already_deleted_old_command(self):
        self.bundle.request("POST", "/applications/100/guilds/300/commands", {"id": "600"})
        old_delete = self.bundle.request("DELETE", "/applications/100/guilds/300/commands/600", status=204)
        ref = self.bundle.request("POST", "/applications/100/guilds/300/commands", {"id": "601"})
        self.bundle.request("DELETE", "/applications/100/guilds/300/commands/601", status=204)
        self.bundle.manifest["temporary_commands"] = {
            "300": {"id": "601", "create_ref": ref, "deleted": True}
        }
        self.assertTrue(self.bundle.save()["valid"])
        self.bundle.rows["rest"][int(old_delete.split(":")[1]) - 1]["status"] = 403
        self.assertIn("temporary_command_not_cleaned", self.codes(self.bundle.save()))

    def test_observed_actor_and_guild_claims_match_raw_invocation(self):
        self.bundle.manifest.update(other_user_id="201", other_guild_id="301")
        for sequence, (actor, guild) in enumerate((("200", "300"), ("201", "301")), 1):
            ref = self.bundle.record(
                "interactions",
                sequence=sequence,
                payload={
                    "id": str(700 + sequence),
                    "application_id": "100",
                    "guild_id": guild,
                    "member": {"user": {"id": actor}},
                    "entitlements": [],
                },
            )
            self.bundle.manifest["cases"][actor] = {
                "status": "observed",
                "interactions": [ref],
                "expected_actor_id": actor,
                "expected_guild_id": guild,
            }
        self.assertTrue(self.bundle.save()["valid"])
        case = self.bundle.manifest["cases"]["201"]
        for field, wrong in (
            ("expected_actor_id", "200"),
            ("expected_guild_id", "300"),
            ("expected_guild_id", "999"),
        ):
            with self.subTest(field=field, wrong=wrong):
                original = case[field]
                case[field] = wrong
                self.assertIn("unsupported_interaction_identity_claim", self.codes(self.bundle.save()))
                case[field] = original

    def test_checkpoint_uses_accepted_invocation_not_extra_authorized_actor(self):
        self.bundle.manifest["other_user_id"] = "201"
        refs = [
            self.bundle.record(
                "interactions",
                sequence=sequence,
                payload={
                    "id": str(700 + sequence),
                    "application_id": "100",
                    "guild_id": "300",
                    "member": {"user": {"id": actor}},
                    "entitlements": [],
                },
            )
            for sequence, actor in enumerate(("201", "200"), 1)
        ]
        receipt = self.bundle.record("actions", event="probe_received", interaction_id="702", actor_id="200")
        self.bundle.manifest["cases"]["probe"] = {
            "status": "observed",
            "interactions": refs,
            "actions": [receipt],
            "expected_actor_id": "200",
            "expected_guild_id": "300",
        }
        self.assertTrue(self.bundle.save()["valid"])
        self.bundle.rows["actions"][0]["interaction_id"] = "701"
        self.assertIn("unsupported_interaction_identity_claim", self.codes(self.bundle.save()))
        self.bundle.rows["actions"][0]["interaction_id"] = "999"
        self.assertIn("unsupported_interaction_identity_claim", self.codes(self.bundle.save()))

    def manual_purchase(self, kind=4):
        self.bundle.manifest.update(manual_test_purchase_ids=["500"], config={"test_mode_confirmed": True})
        baseline = self.bundle.request(
            "GET",
            "/applications/100/entitlements",
            [],
            query={"user_id": "200", "sku_ids": "400", "exclude_deleted": "false"},
        )
        row = {
            "id": "500",
            "application_id": "100",
            "sku_id": "400",
            "user_id": "200",
            "type": kind,
            "deleted": False,
            "consumed": False,
        }
        verification = self.bundle.request("GET", "/applications/100/entitlements/500", row)
        delete = self.bundle.request("DELETE", "/applications/100/entitlements/500", status=204)
        consume = self.bundle.request(
            "POST", "/applications/100/entitlements/500/consume", {"code": 10029}, status=404
        )
        case = {
            "status": "observed",
            "explicit_manual_test_purchase": True,
            "baseline_ids": [],
            "baseline_refs": [baseline],
            "verification_ref": verification,
            "selected_entitlement_id": "500",
            "rest": [delete, consume],
        }
        self.bundle.manifest["cases"]["checkout.deleted_purchase"] = case
        return case

    def test_fresh_manual_test_purchase_can_be_deleted_and_consumed_without_ownership(self):
        case = self.manual_purchase()
        self.assertTrue(self.bundle.save()["valid"])
        for field, bad in (
            ("baseline_ids", ["500"]),
            ("baseline_refs", ["rest.jsonl:1"]),
            ("verification_ref", "rest.jsonl:1"),
            ("explicit_manual_test_purchase", False),
        ):
            with self.subTest(field=field):
                original = case[field]
                case[field] = bad
                codes = self.codes(self.bundle.save())
                self.assertIn("unowned_cleanup", codes)
                self.assertIn("unverified_consume_target", codes)
                case[field] = original
        self.bundle.manifest["config"]["test_mode_confirmed"] = False
        self.assertIn("unowned_cleanup", self.codes(self.bundle.save()))
        self.bundle.manifest["purchase_entitlement_ids"] = ["500"]
        self.assertIn("unverified_consume_target", self.codes(self.bundle.save()))

    def test_manual_proof_requires_actual_baseline_and_authorized_initial_snapshot(self):
        self.manual_purchase()
        baseline = self.bundle.rows["rest"][1]["response"]
        baseline.append({"id": "500"})
        self.assertIn("unowned_cleanup", self.codes(self.bundle.save()))
        baseline.clear()
        snapshot = self.bundle.rows["rest"][2]["response"]
        for field, bad in (
            ("user_id", "999"),
            ("application_id", "999"),
            ("deleted", True),
            ("consumed", True),
        ):
            with self.subTest(field=field):
                original = snapshot[field]
                snapshot[field] = bad
                self.assertIn("unowned_cleanup", self.codes(self.bundle.save()))
                snapshot[field] = original

    def test_manual_purchase_never_authorizes_live_entitlements_or_cleanup(self):
        self.manual_purchase(kind=8)
        self.assertIn("unowned_cleanup", self.codes(self.bundle.save()))
        self.assertIn("unverified_consume_target", self.codes(self.bundle.save()))
        self.bundle.rows["rest"][2]["response"]["type"] = 4
        self.bundle.rows["requests"][-2]["action"] = "cleanup"
        self.assertIn("unowned_cleanup", self.codes(self.bundle.save()))

    def test_manual_live_rows_can_remain_after_cancellation_but_never_be_owned(self):
        self.bundle.manifest["manual_live_entitlement_ids"] = ["500"]
        row = {"id": "500", "application_id": "100", "sku_id": "400", "user_id": "200", "type": 8}
        self.bundle.request("GET", "/applications/100/entitlements/500", row)
        self.bundle.request(
            "GET",
            "/applications/100/skus/400/subscriptions/600",
            {"id": "600", "sku_ids": ["400"], "status": 2, "canceled_at": "2026-10-21T00:00:00Z"},
        )
        self.assertTrue(self.bundle.save()["valid"])
        body = {"sku_id": "400", "owner_id": "200", "owner_type": 2}
        self.bundle.request("POST", "/applications/100/entitlements", row, body=body)
        self.bundle.request("DELETE", "/applications/100/entitlements/500", status=204)
        self.bundle.manifest["owned_entitlements"]["500"] = {**body, "deleted": True}
        codes = self.codes(self.bundle.save())
        self.assertIn("invalid_create_provenance", codes)
        self.assertIn("manual_entitlement_claimed_owned", codes)
        self.assertIn("unowned_cleanup", codes)

    def test_cli_status_contract(self):
        import contextlib
        import io

        self.bundle.save()
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(validator.main([str(self.bundle.directory)]), 0)
        self.assertTrue(json.loads(output.getvalue())[0]["valid"])
        self.bundle.manifest["status"] = "uncertain_write"
        self.bundle.save()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(validator.main([str(self.bundle.directory)]), 1)

    def test_manual_purchase_baseline_requires_both_completed_cursor_branches(self):
        self.bundle.manifest.update(manual_test_purchase_ids=["500"], config={"test_mode_confirmed": True})
        query = {"user_id": "200", "sku_ids": "400", "exclude_deleted": "false", "limit": "100"}
        refs = [
            self.bundle.request(
                "GET",
                "/applications/100/entitlements",
                [{"id": str(eid)} for eid in range(150, 50, -1)],
                query=query,
            ),
            self.bundle.request(
                "GET",
                "/applications/100/entitlements",
                [{"id": str(eid)} for eid in range(50, 0, -1)],
                query={**query, "before": "51"},
            ),
            self.bundle.request("GET", "/applications/100/entitlements", [], query={**query, "after": "150"}),
        ]
        verification = self.bundle.request(
            "GET",
            "/applications/100/entitlements/500",
            {
                "id": "500",
                "application_id": "100",
                "sku_id": "400",
                "user_id": "200",
                "type": 4,
                "deleted": False,
                "consumed": False,
            },
        )
        deletion = self.bundle.request("DELETE", "/applications/100/entitlements/500", status=204)
        case = {
            "status": "observed",
            "explicit_manual_test_purchase": True,
            "baseline_ids": [str(eid) for eid in range(1, 151)],
            "baseline_refs": refs,
            "verification_ref": verification,
            "selected_entitlement_id": "500",
            "rest": [deletion],
        }
        self.bundle.manifest["cases"]["manual"] = case
        self.assertTrue(self.bundle.save()["valid"])
        case["baseline_refs"] = refs[:2]
        self.assertIn("unowned_cleanup", self.codes(self.bundle.save()))

    def test_subscription_gateway_events_bind_authorized_user_and_catalog(self):
        self.bundle.manifest["skus"] = [{"id": "400"}]
        ref = self.bundle.record(
            "gateway",
            payload={
                "op": 0,
                "t": "SUBSCRIPTION_UPDATE",
                "s": 1,
                "d": {"id": "900", "user_id": "200", "sku_ids": ["400"], "status": 1},
            },
        )
        self.bundle.manifest["cases"]["subscription"] = {"status": "observed", "gateway": [ref]}
        self.assertTrue(self.bundle.save()["valid"])
        self.bundle.rows["gateway"][0]["payload"]["d"]["user_id"] = "999"
        self.assertIn("subscription_identity_mismatch", self.codes(self.bundle.save()))

    def api_grant(self, payload_changes=None, body_changes=None, missing_type=False):
        payload = {
            "id": "500",
            "application_id": "100",
            "sku_id": "400",
            "user_id": "200",
            "type": 8,
        }
        body = {"sku_id": "400", "owner_id": "200", "owner_type": 2}
        payload.update(payload_changes or {})
        body.update(body_changes or {})
        if missing_type:
            payload.pop("type")
        ref = self.bundle.request("POST", "/applications/100/entitlements", payload, body=body)
        self.bundle.request("DELETE", "/applications/100/entitlements/500", status=204)
        self.bundle.manifest["owned_entitlements"]["500"] = {**body, "deleted": True}
        return ref

    def test_legacy_creation_keeps_ordered_correlation_and_partial_facts(self):
        self.bundle = Bundle(Path(self.temp.name), version=1)
        self.api_grant()
        self.bundle.manifest["created_entitlement_ids"] = ["500"]
        report = self.bundle.save()
        self.assertTrue(report["valid"], report)
        self.assertTrue(report["legacy"])
        self.assertIn("legacy_partial_evidence", {item["code"] for item in report["warnings"]})
        self.assertFalse(report["coverage"]["pagination"]["calibrated"])
        self.bundle.rows["rest"][1]["response"].pop("type")
        report = self.bundle.save()
        self.assertIn("invalid_create_provenance", self.codes(report))
        self.assertIn("unowned_cleanup", self.codes(report))

    def test_api_cleanup_proof_requires_exact_type_and_correlated_requested_scope(self):
        variants = [
            ({"type": 4}, {}, False),
            ({"type": True}, {}, False),
            ({"type": "8"}, {}, False),
            ({"type": 8.0}, {}, False),
            ({}, {}, True),
            ({"sku_id": "401"}, {}, False),
            ({"application_id": "101"}, {}, False),
            ({"user_id": "201"}, {}, False),
            ({}, {"owner_type": True}, False),
            ({"guild_id": "300"}, {}, False),
            ({}, {"owner_type": 1, "owner_id": "300"}, False),
        ]
        for payload, body, missing in variants:
            with self.subTest(payload=payload, body=body, missing=missing):
                self.bundle = Bundle(Path(self.temp.name))
                self.bundle.manifest["other_user_id"] = "201"
                self.api_grant(payload, body, missing)
                report = self.bundle.save()
                self.assertIn("invalid_create_provenance", self.codes(report))
                self.assertIn("unowned_cleanup", self.codes(report))
                self.assertIn("missing_create_provenance", self.codes(report))

    def test_exact_guild_test_grant_has_a_separate_requested_scope(self):
        self.api_grant({"user_id": None, "guild_id": "300"}, {"owner_type": 1, "owner_id": "300"})
        self.assertTrue(self.bundle.save()["valid"])

    def test_duplicate_create_and_manual_ids_never_gain_new_ownership(self):
        self.api_grant()
        self.bundle.request(
            "POST",
            "/applications/100/entitlements",
            {"id": "500", "application_id": "100", "sku_id": "400", "user_id": "200", "type": 8},
            body={"sku_id": "400", "owner_id": "200", "owner_type": 2},
        )
        self.assertIn("invalid_create_provenance", self.codes(self.bundle.save()))
        self.bundle.manifest["manual_test_purchase_ids"] = ["500"]
        report = self.bundle.save()
        self.assertIn("invalid_create_provenance", self.codes(report))
        self.assertIn("unowned_cleanup", self.codes(report))
        self.assertIn("manual_entitlement_claimed_owned", self.codes(report))

    def test_unknown_or_uncorrelated_success_cannot_prove_cleanup(self):
        for unknown in (True, False):
            with self.subTest(unknown=unknown):
                self.bundle = Bundle(Path(self.temp.name))
                self.api_grant()
                response = self.bundle.rows["rest"][1]
                if unknown:
                    response["outcome_unknown"] = True
                else:
                    response["body"] = {"sku_id": "different"}
                report = self.bundle.save()
                expected = "unresolved_mutation" if unknown else "correlation_mismatch"
                self.assertIn(expected, self.codes(report))
                self.assertIn("unowned_cleanup", self.codes(report))

    def test_missing_schema2_identity_cannot_use_anonymous_response(self):
        self.api_grant()
        self.bundle.rows["requests"][1].pop("request_id")
        self.bundle.rows["rest"][1].pop("request_id")
        report = self.bundle.save()
        self.assertIn("missing_request_id", self.codes(report))
        self.assertIn("unowned_cleanup", self.codes(report))

    def test_late_malformed_shapes_keep_earlier_diagnostics_and_locations(self):
        secret = "never-echo-this-captured-secret"
        self.bundle.manifest["token"] = secret
        ref = self.bundle.record("interactions", payload={"member": [secret]}, sequence=1)
        self.bundle.manifest["cases"]["late"] = {
            "status": "observed",
            "interactions": [ref],
            "expected_actor_id": "200",
        }
        report = self.bundle.save()
        self.assertIn("unredacted_secret", self.codes(report))
        self.assertIn(
            {"code": "malformed_evidence_structure", "location": "interactions.jsonl:1"},
            report["errors"],
        )
        self.assertIn("unsupported_interaction_identity_claim", self.codes(report))
        self.assertEqual(report["coverage"]["rest_outcomes"], 1)
        self.assertNotIn(secret, json.dumps(report))
        self.assertNotIn({"code": "malformed_evidence_structure", "location": "bundle"}, report["errors"])

    def test_malformed_case_references_and_health_values_are_located(self):
        self.bundle.manifest["pending_write"] = {}
        ref = self.bundle.record(
            "health",
            event="observation_end",
            action=[],
            monotonic_ns=None,
        )
        self.bundle.manifest["cases"]["bad"] = {"status": "observed", "health": [ref, {}]}
        report = self.bundle.save()
        self.assertIn("unresolved_mutation", self.codes(report))
        self.assertIn("invalid_references", self.codes(report))
        self.assertIn({"code": "malformed_evidence_structure", "location": ref}, report["errors"])
        self.assertEqual(report["coverage"]["rest_outcomes"], 1)

    def test_cli_usage_and_multi_bundle_report_contract(self):
        import contextlib
        import io

        self.bundle.save()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            validator.main([])
        self.assertEqual(raised.exception.code, 2)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = validator.main([str(self.bundle.directory), str(self.bundle.directory / "missing")])
        reports = json.loads(output.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual(len(reports), 2)
        self.assertTrue(reports[0]["valid"])
        self.assertFalse(reports[1]["valid"])


if __name__ == "__main__":
    unittest.main()
