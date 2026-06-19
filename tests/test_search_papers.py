import json

from search_papers.clinicaltrials import load_shards
from search_papers.preprocess import studies_to_papers
from search_papers import pubmed


def _study(references, *, nct="NCT00000001"):
    return {
        "protocolSection": {
            "identificationModule": {"nctId": nct},
            "statusModule": {
                "primaryCompletionDateStruct": {"date": "2020-01"},
                "completionDateStruct": {"date": "2020-06"},
            },
            "referencesModule": {"references": references},
        }
    }


def test_studies_to_papers_filters_ref_types_and_requires_pmid_citation():
    studies = [
        _study(
            [
                {"type": "DERIVED", "citation": "Paper A", "pmid": "111"},
                {"type": "RESULT", "citation": "Paper B", "pmid": "222"},
                {"type": "BACKGROUND", "citation": "Paper C", "pmid": "333"},  # dropped: type
                {"type": "DERIVED", "citation": "No pmid"},  # dropped: no pmid
            ]
        ),
        {"protocolSection": {"referencesModule": {}}},  # dropped: no references
    ]
    papers = studies_to_papers(studies)

    assert [p.pmid for p in papers] == ["111", "222"]
    a = papers[0]
    assert a.nct_id == "NCT00000001"
    assert a.paper_title == "Paper A"
    assert a.ref_type == "DERIVED"
    assert a.primary_completion_date == "2020-01"
    assert a.study_completion_date == "2020-06"


def test_paper_dumps_legacy_json_keys():
    papers = studies_to_papers([_study([{"type": "RESULT", "citation": "X", "pmid": "9"}])])
    dumped = papers[0].model_dump(by_alias=True)
    assert dumped["nctId"] == "NCT00000001"
    assert dumped["paper_title"] == "X"
    assert dumped["pmid"] == "9"


def test_load_shards_reads_all(tmp_path):
    (tmp_path / "shard_00001.json").write_text(json.dumps([{"a": 1}]))
    (tmp_path / "shard_00002.json").write_text(json.dumps([{"a": 2}, {"a": 3}]))
    assert list(load_shards(tmp_path)) == [{"a": 1}, {"a": 2}, {"a": 3}]


def test_get_pubmed_data_batch_extracts_journal_and_date(monkeypatch):
    payload = {
        "result": {
            "uids": ["111"],
            "111": {
                "fulljournalname": "Journal of Tests",
                "history": [
                    {"pubstatus": "received", "date": "2019-01-01"},
                    {"pubstatus": "pubmed", "date": "2020-02-02"},
                ],
            },
        }
    }

    class _Resp:
        def json(self):
            return payload

    monkeypatch.setattr(pubmed.requests, "get", lambda *a, **k: _Resp())
    monkeypatch.setattr(pubmed.time, "sleep", lambda *a, **k: None)

    out = pubmed.get_pubmed_data_batch(["111"])
    assert out["111"]["journal_name"] == "Journal of Tests"
    assert out["111"]["date"] == "2020-02-02"
