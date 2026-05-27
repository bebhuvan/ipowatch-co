from types import SimpleNamespace

from ipo_portal.filing_processor import (
    PDFText,
    _liteparse_pages,
    _pdf_text_from_pages,
    _slice_with_page_markers,
    assess_extraction_quality,
    redact_unshaped_facts,
    validate_and_redact_citations,
)


def test_validate_and_redact_repairs_wrong_page() -> None:
    doc = {
        "facts": {
            "business": {
                "company_description": {
                    "value": "Manufactures widgets",
                    "raw_excerpt": "The Company manufactures widgets for industrial customers.",
                    "source_page": 2,
                    "source_section": "Our Business",
                    "confidence": "high",
                }
            }
        }
    }
    pdf = PDFText(
        text="The Company manufactures widgets for industrial customers.\fOther page",
        pages=["The Company manufactures widgets for industrial customers.", "Other page"],
    )
    report = validate_and_redact_citations(doc, pdf)
    assert report["state"] == "clean"
    assert report["repaired_count"] == 1
    assert doc["facts"]["business"]["company_description"]["source_page"] == 1


def test_validate_and_redact_redacts_unverified_fact() -> None:
    doc = {
        "facts": {
            "risks": {
                "red_flags": [
                    {
                        "value": "High customer concentration",
                        "raw_excerpt": "not present in pdf",
                        "source_page": 1,
                        "source_section": "Risk Factors",
                        "confidence": "high",
                    }
                ]
            }
        }
    }
    pdf = PDFText(text="Different text", pages=["Different text"])
    report = validate_and_redact_citations(doc, pdf)
    leaf = doc["facts"]["risks"]["red_flags"][0]
    assert report["state"] == "clean_with_redactions"
    assert report["redacted_count"] == 1
    assert leaf["value"] is None
    assert leaf["raw_excerpt"] is None
    assert leaf["source_page"] is None
    assert leaf["confidence"] == "low"


def test_pdf_text_from_pages_preserves_page_boundaries() -> None:
    pdf = _pdf_text_from_pages(["First page", "Second page"], "liteparse")

    assert pdf.extractor == "liteparse"
    assert pdf.pages == ["First page", "Second page"]
    assert pdf.text == "First page\fSecond page"


def test_liteparse_pages_preserve_reported_page_numbers() -> None:
    pages = _liteparse_pages(
        [
            SimpleNamespace(pageNum=1, text="First"),
            SimpleNamespace(pageNum=3, text="Third"),
        ]
    )

    assert pages == ["First", "", "Third"]


def test_slice_with_page_markers_preserves_source_page_numbers() -> None:
    pdf = PDFText(text="A\fB\fC", pages=["A", "B", "C"])

    text = _slice_with_page_markers(pdf, 2, 3)

    assert "--- PDF PAGE 2 ---" in text
    assert "--- PDF PAGE 3 ---" in text
    assert "B" in text
    assert "C" in text


def test_assess_extraction_quality_fails_missing_required_section() -> None:
    doc = {
        "facts": {},
        "deepseek": {"calls": [{"section": "business", "slice_method": "not_found", "skipped": True}]},
        "citation_validation": {"checked_count": 0, "redacted_count": 0, "repaired_count": 0},
    }

    quality = assess_extraction_quality(doc)

    assert quality["state"] == "fail"
    assert quality["missing_required_sections"] == ["business"]


def test_assess_extraction_quality_reviews_high_repair_rate() -> None:
    doc = {
        "facts": {
            "business": {
                "company_description": {
                    "value": "Makes widgets",
                    "raw_excerpt": "Makes widgets",
                    "source_page": 1,
                    "source_section": "Business",
                    "confidence": "high",
                }
            }
        },
        "deepseek": {"calls": []},
        "citation_validation": {"checked_count": 30, "redacted_count": 3, "repaired_count": 4},
    }

    quality = assess_extraction_quality(doc)

    assert quality["state"] == "review"
    assert quality["repair_rate"] == 0.1333


def test_redact_unshaped_facts_removes_primitive_leaves() -> None:
    doc = {"facts": {"financials": {"periods": ["FY24", None, {"value": "FY23"}]}}}

    report = redact_unshaped_facts(doc)

    assert report["redacted_count"] == 1
    assert doc["facts"]["financials"]["periods"] == [None, None, {"value": "FY23"}]
