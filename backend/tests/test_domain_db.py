"""Database-backed checks for the full solve pipeline.

These run only when ``TEST_DATABASE_URL`` points at a PostgreSQL database; the
pure chain and batch rules are covered by ``test_chains.py`` without a database.

    TEST_DATABASE_URL=postgresql+psycopg://app@127.0.0.1:5432/workflow \
        python -m unittest discover -s tests -t .
"""

from copy import deepcopy
import os
import unittest

from src.services.domain import SPEC, solve


URL = os.getenv("TEST_DATABASE_URL")


@unittest.skipUnless(URL, "set TEST_DATABASE_URL to run database tests")
class SolveTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from sqlalchemy import create_engine
        cls.engine = create_engine(URL)

    def solve(self, payload):
        return solve(payload, self.engine)

    def section(self, result, title):
        return next(row for row in result["sections"] if row["title"] == title)

    def test_sample_matches_expected_metrics(self):
        result = self.solve(deepcopy(SPEC["sample"]))
        self.assertEqual(result["metrics"], SPEC["expect"])

    def test_coverage_lists_basis_and_breakpoints_per_screening(self):
        result = self.solve(deepcopy(SPEC["sample"]))
        rows = {row["场次编号"]: row for row in self.section(result, "逐场覆盖依据与断点")["rows"]}
        self.assertEqual(rows["P-101"]["结论"], "完整覆盖")
        self.assertEqual(rows["P-101"]["断点"], [])
        self.assertEqual(rows["P-103"]["结论"], "存在断点")
        self.assertEqual([gap["分钟"] for gap in rows["P-103"]["断点"]], [10, 20])
        # The chain is spelled out from the rights holder to the exhibitor.
        self.assertIn("C-03 v1（潮幕版权→上海云幕）", rows["P-103"]["覆盖依据"])
        self.assertIn("R-07 v1（上海云幕→潮幕线上）", rows["P-103"]["覆盖依据"])

    def test_multiple_chains_stitch_inside_one_screening(self):
        payload = deepcopy(SPEC["sample"])
        payload["supplement_batches"] = []
        # Drop the chain-fed grants that cover 19:00-20:15 and keep only R-10
        # (rights holder -> cinema) to confirm the direct chain stands alone.
        payload["rights"] = [
            row for row in payload["rights"]
            if row["right_id"] in ("R-10", "C-02", "D-02", "R-05", "R-06",
                                   "C-03", "R-07", "C-04", "R-08")
        ]
        result = self.solve(payload)
        rows = {row["场次编号"]: row for row in self.section(result, "逐场覆盖依据与断点")["rows"]}
        self.assertEqual(rows["P-101"]["覆盖分段"],
                         [{"时段": "20:15–20:30", "授权": "R-10"}])
        self.assertEqual(rows["P-101"]["未授权分钟"], 75)

    def test_chain_clipping_flows_into_gaps(self):
        payload = deepcopy(SPEC["sample"])
        for row in payload["rights"]:
            if row["right_id"] == "C-01" and row["contract_version"] == 2:
                row["ends_at"] = "2026-12-04T20:00+08:00"
        result = self.solve(payload)
        rows = {row["场次编号"]: row for row in self.section(result, "逐场覆盖依据与断点")["rows"]}
        # R-04 (20:00-20:15) sat outside what C-01 v2 still granted.
        self.assertEqual(rows["P-101"]["未授权分钟"], 15)
        self.assertEqual(rows["P-101"]["覆盖分段"][-1]["授权"], "R-10")

    def test_corrected_contract_keeps_original_version_on_ticket(self):
        result = self.solve(deepcopy(SPEC["sample"]))
        tickets = {row["缺口单编号"]: row for row in self.section(result, "原排片权益缺口单")["rows"]}
        ticket = tickets["P-102-G1"]
        self.assertEqual(ticket["原合同版本"], "R-05 v1、R-06 v1")
        self.assertIn("R-05 v1 → v2（已更正）", ticket["现行版本"])
        self.assertEqual(ticket["补件状态"], "已补齐")

    def test_cycle_grants_no_coverage(self):
        payload = deepcopy(SPEC["sample"])
        payload["rights"] = [row for row in payload["rights"] if row["right_id"] in ("X-01", "X-02")]
        payload["screenings"] = payload["screenings"][:1]
        payload["supplement_batches"] = []
        result = self.solve(payload)
        self.assertEqual(result["metrics"]["未授权分钟"], 90)
        chains_section = {row["授权编号"]: row for row in self.section(result, "合同链校核")["rows"]}
        self.assertEqual(chains_section["X-01"]["状态"], "循环转授")

    def test_grant_to_an_agent_does_not_cover_the_cinema(self):
        payload = deepcopy(SPEC["sample"])
        payload["supplement_batches"] = []
        # Keep only the chain down to the distributor; no grant reaches the
        # cinema showing the film, so the screening is uncovered.
        payload["rights"] = [
            row for row in payload["rights"] if row["right_id"] in ("C-02", "D-02")
        ]
        result = self.solve(payload)
        rows = {row["场次编号"]: row for row in self.section(result, "逐场覆盖依据与断点")["rows"]}
        self.assertEqual(rows["P-102"]["未授权分钟"], 60)
        self.assertEqual(rows["P-102"]["覆盖依据"], "无有效授权链")

    def test_empty_screenings_return_zero(self):
        payload = deepcopy(SPEC["sample"])
        payload["screenings"] = []
        result = self.solve(payload)
        self.assertEqual(result["metrics"]["排片场次"], 0)
        self.assertEqual(result["metrics"]["缺口段数"], 0)


if __name__ == "__main__":
    unittest.main()
