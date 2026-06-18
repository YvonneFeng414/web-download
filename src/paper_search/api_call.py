import requests
import time
import json

CLINICAL_TRIAL_STUDIES_URL = "https://clinicaltrials.gov/api/v2/studies"



def fetch_all_results(query_params, max_results= 562284, page_size=1000):
    all_studies = []
    page_token = None
    page_count = 0

    params = {
        'pageSize': page_size,
        'countTotal': 'true',
        **query_params
    }

    print(params)

    session = requests.Session()
    while len(all_studies) < max_results:
        if page_token:
            params['pageToken'] = page_token

        try:
            response = session.get(CLINICAL_TRIAL_STUDIES_URL, params=params, timeout=10)
            data = response.json()

            studies = data.get('studies', [])
            all_studies.extend(studies)
            cur_num_studies = page_count * page_size + len(studies)
            page_count += 1
            
            print(f"pg cnt: {page_count}, fetched: {cur_num_studies}/{max_results}")

            page_token = data.get('nextPageToken')
            if not page_token:
                print("not more page")
                break
            
            time.sleep(0.5)

        except requests.exceptions.RequestException as e:
            print(f"error on page {page_count}: {e}")
            print(f"Status Code: {response.status_code}")
            print(f"Content-Type: {response.headers.get('Content-Type')}")
            print(f"Response Text (first 500 chars): {response.text[:500]}")

            break


    return all_studies

if __name__ == "__main__":
    query_params = {
        'filter.overallStatus': 'COMPLETED',
        # 'filter.advanced': 'AREA[LargeDocHasSAP]true OR AREA[LargeDocHasProtocol]true',
        'fields': 'NCTId,protocolSection.referencesModule,protocolSection.statusModule,protocolSection.contactsLocationsModule'
    }

    data = fetch_all_results(query_params)
    
    with open('studies.json', 'w') as f:
        json.dump(data, f, indent=2)