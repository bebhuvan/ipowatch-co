from scripts.extract_rich_rhp import PDFText, repair_and_validate_citations


def _pdf(*pages: str) -> PDFText:
    text = "\f".join(pages)
    return PDFText(text=text, pages=list(pages), char_to_page=[])


def test_repair_and_validate_citations_repairs_wrong_page() -> None:
    doc = {
        "hero": {
            "total_offer": {
                "value": 210000000,
                "raw_excerpt": "AGGREGATING UP TO ` 2,100 LAKH",
                "source_page": 75,
                "confidence": "high",
            }
        }
    }
    report = repair_and_validate_citations(
        doc,
        _pdf("AGGREGATING UP TO ` 2,100 LAKH", "other page"),
    )
    assert report["state"] == "clean"
    assert report["repaired_count"] == 1
    assert doc["hero"]["total_offer"]["source_page"] == 1


def test_repair_and_validate_citations_redacts_unresolved_excerpt() -> None:
    doc = {
        "leaf": {
            "value": "x",
            "raw_excerpt": "not in pdf",
            "source_page": 1,
            "confidence": "high",
        }
    }
    report = repair_and_validate_citations(doc, _pdf("different text"))
    assert report["state"] == "clean_with_redactions"
    assert report["unresolved_count"] == 1
    assert report["redacted_count"] == 1
    assert doc["leaf"]["value"] is None
    assert doc["leaf"]["raw_excerpt"] is None
    assert doc["leaf"]["source_page"] is None
    assert doc["leaf"]["confidence"] == "low"
