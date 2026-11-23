"""Unit tests for delegation chains and supplement batches.

Run with ``python -m unittest discover -s tests -t .`` from ``backend/``.
The chain and batch rules are pure functions, so no database is needed.
"""

from datetime import datetime, timedelta, timezone
import unittest

from src.services import chains, supplements


DESK = timezone(timedelta(hours=8))
DAY = "2026-12-04"


def moment(clock):
    return datetime.fromisoformat(f"{DAY}T{clock}:00+08:00")


def grant(right_id, starts, ends, territory="上海", medium="影院", film="F-01",
          grantor="版权方", grantee="代理", upstream=None, version=1):
    return {
        "right_id": right_id,
        "contract_version": version,
        "film_id": film,
        "territory": territory,
        "medium": medium,
        "grantor": grantor,
        "grantee": grantee,
        "upstream_right_id": upstream,
        "starts_at": moment(starts),
        "ends_at": moment(ends),
    }


def by_id(resolved):
    return {row["right_id"]: row for row in resolved}


class ChainResolutionTest(unittest.TestCase):
    def test_full_chain_is_effective_and_reported_root_first(self):
        resolved = by_id(chains.resolve([
            grant("C", "18:00", "22:00", grantor="版权方", grantee="总代"),
            grant("D", "18:30", "21:00", grantor="总代", grantee="院线", upstream="C"),
            grant("R", "19:00", "20:30", grantor="院线", grantee="影城", upstream="D"),
        ]))
        self.assertEqual(resolved["R"]["status"], chains.STATUS_VALID)
        self.assertEqual([row["right_id"] for row in resolved["R"]["chain"]], ["C", "D", "R"])

    def test_download_is_clipped_to_every_upstream_window(self):
        resolved = by_id(chains.resolve([
            grant("C", "18:00", "22:00", grantor="版权方", grantee="总代"),
            grant("D", "18:30", "21:00", grantor="总代", grantee="院线", upstream="C"),
            grant("R", "19:00", "23:00", grantor="院线", grantee="影城", upstream="D"),
        ]))
        self.assertEqual(resolved["R"]["status"], chains.STATUS_VALID)
        self.assertEqual(resolved["R"]["effective_start"], moment("19:00"))
        self.assertEqual(resolved["R"]["effective_end"], moment("21:00"))

    def test_territory_outside_upstream_is_invalid(self):
        resolved = by_id(chains.resolve([
            grant("C", "18:00", "22:00", territory="上海", grantor="版权方", grantee="总代"),
            grant("D", "18:30", "21:00", territory="杭州", grantor="总代", grantee="院线",
                  upstream="C"),
        ]))
        self.assertEqual(resolved["D"]["status"], chains.STATUS_TERRITORY_OUTSIDE)

    def test_wildcard_scope_is_narrowed_not_extended(self):
        resolved = by_id(chains.resolve([
            grant("C", "18:00", "22:00", territory="杭州", medium="线上",
                  grantor="版权方", grantee="总代"),
            grant("D", "18:30", "21:00", territory="*", medium="*",
                  grantor="总代", grantee="院线", upstream="C"),
        ]))
        self.assertEqual(resolved["D"]["status"], chains.STATUS_VALID)
        self.assertEqual(resolved["D"]["effective_territory"], "杭州")
        self.assertEqual(resolved["D"]["effective_medium"], "线上")

    def test_medium_outside_upstream_is_invalid(self):
        resolved = by_id(chains.resolve([
            grant("C", "18:00", "22:00", medium="影院", grantor="版权方", grantee="总代"),
            grant("D", "18:30", "21:00", medium="线上", grantor="总代", grantee="院线",
                  upstream="C"),
        ]))
        self.assertEqual(resolved["D"]["status"], chains.STATUS_MEDIUM_OUTSIDE)

    def test_film_outside_upstream_is_invalid(self):
        resolved = by_id(chains.resolve([
            grant("C", "18:00", "22:00", film="F-01", grantor="版权方", grantee="总代"),
            grant("D", "18:30", "21:00", film="F-02", grantor="总代", grantee="院线",
                  upstream="C"),
        ]))
        self.assertEqual(resolved["D"]["status"], chains.STATUS_FILM_OUTSIDE)

    def test_window_without_overlap_is_invalid(self):
        resolved = by_id(chains.resolve([
            grant("C", "18:00", "19:00", grantor="版权方", grantee="总代"),
            grant("D", "20:00", "21:00", grantor="总代", grantee="院线", upstream="C"),
        ]))
        self.assertEqual(resolved["D"]["status"], chains.STATUS_WINDOW_OUTSIDE)

    def test_missing_upstream_is_invalid(self):
        resolved = by_id(chains.resolve([
            grant("D", "18:30", "21:00", grantor="总代", grantee="院线", upstream="C"),
        ]))
        self.assertEqual(resolved["D"]["status"], chains.STATUS_MISSING_UPSTREAM)

    def test_grantor_must_match_upstream_grantee(self):
        resolved = by_id(chains.resolve([
            grant("C", "18:00", "22:00", grantor="版权方", grantee="总代"),
            grant("D", "18:30", "21:00", grantor="别家", grantee="院线", upstream="C"),
        ]))
        self.assertEqual(resolved["D"]["status"], chains.STATUS_GRANTOR_MISMATCH)

    def test_cycle_is_discarded_as_a_whole(self):
        resolved = by_id(chains.resolve([
            grant("A", "18:00", "22:00", grantor="版权方", grantee="甲", upstream="B"),
            grant("B", "18:30", "21:00", grantor="乙", grantee="版权方", upstream="A"),
        ]))
        self.assertEqual(resolved["A"]["status"], chains.STATUS_CYCLE)
        self.assertEqual(resolved["B"]["status"], chains.STATUS_CYCLE)
        self.assertEqual(chains.effective_rows(list(resolved.values())), [])

    def test_cycle_through_an_agent_grants_nothing(self):
        resolved = by_id(chains.resolve([
            grant("C", "18:00", "22:00", grantor="版权方", grantee="总代"),
            grant("A", "18:00", "22:00", grantor="甲", grantee="乙", upstream="B"),
            grant("B", "18:00", "22:00", grantor="乙", grantee="甲", upstream="A"),
            grant("D", "18:00", "22:00", grantor="甲", grantee="院线", upstream="A"),
        ]))
        self.assertEqual(resolved["A"]["status"], chains.STATUS_CYCLE)
        self.assertEqual(resolved["B"]["status"], chains.STATUS_CYCLE)
        self.assertEqual(resolved["D"]["status"], chains.STATUS_CYCLE)
        effective = chains.effective_rows(list(resolved.values()))
        self.assertEqual([row["right_id"] for row in effective], ["C"])

    def test_latest_contract_version_supersedes_older(self):
        resolved = by_id(chains.resolve([
            grant("R", "18:00", "20:00", grantor="院线", grantee="影城", version=1),
            grant("R", "18:00", "21:00", grantor="院线", grantee="影城", version=2),
        ]))
        self.assertEqual(resolved["R"]["active"], True)
        self.assertEqual(resolved["R"]["contract_version"], 2)

    def test_chain_follows_the_latest_upstream_version(self):
        resolved = by_id(chains.resolve([
            grant("C", "18:00", "19:00", grantor="版权方", grantee="总代", version=1),
            grant("C", "18:00", "22:00", grantor="版权方", grantee="总代", version=2),
            grant("D", "20:00", "21:00", grantor="总代", grantee="院线", upstream="C"),
        ]))
        self.assertEqual(resolved["D"]["status"], chains.STATUS_VALID)
        self.assertEqual(resolved["D"]["chain"][0]["contract_version"], 2)


class InstallmentParsingTest(unittest.TestCase):
    def test_same_batch_delivered_in_pieces_is_merged(self):
        from src.services import domain
        payload = {
            "max_delay_minutes": 0,
            "screenings": [dict(screening_id="P", film_id="F", title="t", territory="上海",
                                medium="影院", starts_at="2026-12-04T19:00+08:00",
                                ends_at="2026-12-04T20:00+08:00")],
            "rights": [],
            "supplement_batches": [
                {"batch_id": "B", "confirmed": False, "items": [dict(
                    right_id="S", film_id="F", territory="上海", medium="影院",
                    starts_at="2026-12-04T19:00+08:00", ends_at="2026-12-04T19:30+08:00")]},
                {"batch_id": "B", "confirmed": True, "items": [dict(
                    right_id="T", film_id="F", territory="上海", medium="影院",
                    starts_at="2026-12-04T19:30+08:00", ends_at="2026-12-04T20:00+08:00")]},
            ],
        }
        _, _, batches, _ = domain._read_payload(payload)
        self.assertEqual(len(batches), 1)
        self.assertTrue(batches[0]["confirmed"])
        self.assertEqual([row["right_id"] for row in batches[0]["items"]], ["S", "T"])


class SupplementBatchTest(unittest.TestCase):
    def register(self):
        return {supplements.grant_key(row): row for row in [
            grant("C", "18:00", "22:00", grantor="版权方", grantee="总代"),
            grant("D", "18:30", "21:00", grantor="总代", grantee="院线", upstream="C"),
        ]}

    def batch(self, batch_id, items, confirmed=True):
        return {"batch_id": batch_id, "confirmed": confirmed, "items": items}

    def test_unconfirmed_batch_is_staged_not_switched_in(self):
        register, report, pending, _ = supplements.apply(
            [self.batch("B-1", [grant("R", "19:00", "20:30", grantor="院线", grantee="影城",
                                     upstream="D")], confirmed=False)],
            self.register())
        self.assertEqual(report[0]["状态"], supplements.BATCH_STAGED)
        self.assertNotIn(("R", 1), register)
        self.assertEqual([row["right_id"] for row in pending], ["R"])

    def test_confirmed_batch_switches_in_at_once(self):
        register, report, _, _ = supplements.apply(
            [self.batch("B-1", [grant("R", "19:00", "20:30", grantor="院线", grantee="影城",
                                     upstream="D")])],
            self.register())
        self.assertEqual(report[0]["状态"], supplements.BATCH_EFFECTIVE)
        self.assertIn(("R", 1), register)

    def test_batch_with_invalid_piece_is_returned_whole(self):
        register, report, _, _ = supplements.apply(
            [self.batch("B-1", [
                grant("R", "19:00", "20:30", grantor="院线", grantee="影城", upstream="D"),
                grant("Q", "19:00", "20:30", grantor="院线", grantee="影城", upstream="MISSING"),
            ])],
            self.register())
        self.assertEqual(report[0]["状态"], supplements.BATCH_RETURNED)
        self.assertNotIn(("R", 1), register)
        self.assertNotIn(("Q", 1), register)

    def test_retry_after_failure_registers_each_piece_once(self):
        first = [self.batch("B-1", [
            grant("R", "19:00", "20:30", grantor="院线", grantee="影城", upstream="D"),
            grant("Q", "19:00", "20:30", grantor="院线", grantee="影城", upstream="MISSING"),
        ])]
        register, report, _, _ = supplements.apply(first, self.register())
        self.assertEqual(report[0]["状态"], supplements.BATCH_RETURNED)
        retry = [self.batch("B-2", [
            grant("C2", "18:00", "22:00", grantor="版权方", grantee="另一总代"),
            grant("R", "19:00", "20:30", grantor="院线", grantee="影城", upstream="D"),
        ])]
        register, report, _, _ = supplements.apply(retry, register)
        self.assertEqual(report[0]["状态"], supplements.BATCH_EFFECTIVE)
        self.assertEqual(report[0]["登记件数"], 2)
        resent = [self.batch("B-3", [
            grant("R", "19:00", "20:30", grantor="院线", grantee="影城", upstream="D"),
        ])]
        register, report, _, _ = supplements.apply(resent, register)
        self.assertEqual(report[0]["登记件数"], 0)
        self.assertEqual(report[0]["重复跳过"], 1)

    def test_conflicting_reregistration_needs_a_new_version(self):
        register = {supplements.grant_key(row): row for row in
                    [grant("R", "19:00", "20:30", grantor="院线", grantee="影城")]}
        _, report, _, _ = supplements.apply(
            [self.batch("B-1", [grant("R", "18:00", "20:30", grantor="院线", grantee="影城")])],
            register)
        self.assertEqual(report[0]["状态"], supplements.BATCH_RETURNED)
        self.assertIn("提升合同版本", report[0]["说明"])

    def test_corrected_version_replaces_effective_window(self):
        register = {supplements.grant_key(row): row for row in
                    [grant("R", "19:00", "20:30", grantor="院线", grantee="影城")]}
        register, report, _, _ = supplements.apply(
            [self.batch("B-1", [grant("R", "19:00", "21:00", grantor="院线", grantee="影城",
                                      version=2)])],
            register)
        self.assertEqual(report[0]["状态"], supplements.BATCH_EFFECTIVE)
        self.assertEqual(len(register), 2)
        resolved = by_id(chains.resolve(list(register.values())))
        self.assertTrue(resolved["R"]["active"])
        self.assertEqual(resolved["R"]["contract_version"], 2)


if __name__ == "__main__":
    unittest.main()
