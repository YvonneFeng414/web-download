import requests
import time
import json
from tqdm import tqdm

def get_pubmed_data_batch(pmids):
    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
    params = {
        "db": "pubmed",
        "id": ",".join(str(pmid) for pmid in pmids),
        "retmode": "json"
    }
    r = requests.get(url, params=params).json()
    time.sleep(0.5)
    results = {}
    try:
        for pmid in r['result']['uids']:
            res = r['result'][pmid]
            pub_history = res.get("history", None)
            date = None
            if pub_history:
                for _stage in pub_history:
                    if _stage.get("pubstatus", None) == "pubmed":
                        date = _stage.get("date", None)
            results[pmid] = {
                "journal_name": res.get("fulljournalname", None),
                "date": date
            }
    except KeyError as e:
        print(r)
    return results

def main():
    with open('papers.json', 'r') as f:
        data = json.load(f)

    out = []
        
    batch_size = 200
    pmids = [item['pmid'] for item in data]
    
    for i in tqdm(range(0, len(pmids), batch_size), desc="# paper batches"):
        batch_pmids = pmids[i:i+batch_size]
        batch_data = get_pubmed_data_batch(batch_pmids)
        for item in data[i:i+batch_size]:
            pmid = str(item['pmid'])
            _pubmed_data = batch_data.get(pmid, {})
            item.update(_pubmed_data)
            out.append(item)

    with open('papers.json', 'w') as f:
        json.dump(out, f, indent=2)


if __name__ == "__main__":
    main()