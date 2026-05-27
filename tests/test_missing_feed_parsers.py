from __future__ import annotations

import unittest

from ipo_portal.normalize_v2.parsers.bse_ipo_documents import parse as parse_bse_ipo_documents
from ipo_portal.normalize_v2.parsers.coverage_noop import parse as parse_noop
from ipo_portal.normalize_v2.parsers.nse_public_issue_advertisements import parse as parse_nse_ads
from ipo_portal.normalize_v2.parsers.registry import ParserContext


class MissingFeedParserTests(unittest.TestCase):
    def test_nse_public_issue_advertisements_creates_filed_issue(self) -> None:
        body = [
            {
                "boardType": "SME Board",
                "draftDate": "15-05-2026",
                "issueType": "IPO",
                "issuerName": "Example Ads Limited",
                "panNo": "AAAAA0000A",
                "record20": [
                    {
                        "advertisementType": "Pre-filing of draft offer document",
                        "attFilename": "https://nsearchives.nseindia.com/corporate/example.pdf",
                        "submitDate": "16-05-2026",
                    }
                ],
            }
        ]
        [contribution] = parse_nse_ads(body, ParserContext("nse", "public_issue_advertisements", "2026-05-24T00:00:00+00:00"))
        self.assertEqual(contribution.fields["identity.company_name"], "Example Ads Limited")
        self.assertEqual(contribution.fields["identity.board_type"], "SME Board")
        self.assertEqual(contribution.fields["identity.status"], "Filed")
        self.assertNotIn("documents.drhp_url", contribution.fields)

    def test_bse_ipo_documents_maps_document_urls(self) -> None:
        body = {
            "table": [
                {
                    "Scrip_Name": "Example Documents Limited",
                    "scrip_cd": "123456",
                    "Prior_Id": "268444",
                    "createdby": "362917",
                    "updated_date": "5/16/2026 5:22:33 AM",
                    "DRHP_Doc": "362917/IPO Prior/DRHPANDDAP_20260516051615.zip",
                    "Red_Herring_Prospectus": "https://example.test/rhp.pdf",
                    "Prospectus": "",
                    "T5Stage_Document": "362917/IPO T+5/Basis.pdf",
                }
            ]
        }
        [contribution] = parse_bse_ipo_documents(body, ParserContext("bse", "ipo_documents", "2026-05-24T00:00:00+00:00"))
        self.assertEqual(contribution.fields["identity.status"], "Filed")
        self.assertEqual(contribution.fields["documents.drhp_url"], "https://www.bseindia.com/downloads/ipo/362917/IPO%20Prior/DRHPANDDAP_20260516051615.zip")
        self.assertEqual(contribution.fields["documents.rhp_url"], "https://example.test/rhp.pdf")
        self.assertEqual(contribution.fields["documents.basis_allotment_url"], "https://www.bseindia.com/downloads/ipo/362917/IPO%20T%2B5/Basis.pdf")

    def test_noop_helper_feed_is_intentional(self) -> None:
        self.assertEqual(parse_noop([{"issuerName": "Only Dropdown"}], ParserContext("nse", "public_issue_company_list", "2026-05-24T00:00:00+00:00")), [])


if __name__ == "__main__":
    unittest.main()
