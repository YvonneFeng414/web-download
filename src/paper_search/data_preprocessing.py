import requests
import json
from tqdm import tqdm

from utils.names import full_to_short


with open('studies.json', 'r') as f:
    data = json.load(f)

out = []

for s in tqdm(data, desc="# studies", mininterval=0):
    if 'referencesModule' in s['protocolSection'] and 'references' in s['protocolSection']['referencesModule']:
        nctId = s['protocolSection']['identificationModule']['nctId']
        references = s['protocolSection']['referencesModule']['references']
        
        if "primaryCompletionDateStruct" in s['protocolSection']['statusModule']:
            primary_completion_date = s['protocolSection']['statusModule']['primaryCompletionDateStruct']['date']
        else:
            primary_completion_date = None

        if "completionDateStruct" in s['protocolSection']['statusModule']:
            study_completion_date = s['protocolSection']['statusModule']['completionDateStruct']['date']
        else:
            study_completion_date = None


          # PI extraction
        contacts_mod = s['protocolSection'].get('contactsLocationsModule', {})
        officials = contacts_mod.get('overallOfficials', [])
        # principal_investigators = [
        #     o.get('name')
        #     for o in officials
        #     if o.get('role') == 'PRINCIPAL_INVESTIGATOR'
        # ]

        for r in references:
            if 'citation' not in r or 'pmid' not in r:
                continue
            ref_type = r['type']
            if ref_type not in {'DERIVED', 'RESULT'}:
                continue
            
            # has_pi = False
            # for _pi in principal_investigators:
            #     if full_to_short(_pi) in r['citation']:
            #         has_pi = True
            #         break
            # if not has_pi:
            #     continue

            item = dict()
            item['nctId'] = nctId
            item['paper_title'] = r['citation']
            item['pmid'] = r['pmid']
            item['ref_type'] = ref_type
            item['primary_completion_date'] = primary_completion_date
            item['study_completion_date'] = study_completion_date
            # item['principal_investigators'] = principal_investigators

            
            out.append(item)

with open('papers.json', 'w') as f:
    json.dump(out, f, indent=2)