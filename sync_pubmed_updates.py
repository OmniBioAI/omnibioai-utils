#!/usr/bin/env python3
"""
OmniBioAI PubMed Sync Pipeline
Production-grade incremental sync
Run daily via cron
"""
import ftplib, gzip, json, requests
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime

DATA_DIR = Path("/home/manish/Desktop/machine/data/PubMed/Abstracts")
DOWNLOAD_DIR = Path("/tmp/pubmed_updates")
STATE_FILE = Path("/home/manish/Desktop/machine/data/PubMed/sync_state.json")
DOWNLOAD_DIR.mkdir(exist_ok=True)

def load_state():
    """Load last sync state"""
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {
        "last_file": None,
        "last_sync": None,
        "total_updated": 0,
        "total_new": 0,
        "files_processed": []
    }

def save_state(state):
    """Save sync state"""
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)
    print(f"✅ State saved: {STATE_FILE}")

def get_all_update_files():
    """Get all files from PubMed FTP"""
    ftp = ftplib.FTP('ftp.ncbi.nlm.nih.gov')
    ftp.login()
    ftp.cwd('/pubmed/updatefiles/')
    files = []
    ftp.retrlines('LIST', files.append)
    
    xml_files = []
    for f in files:
        parts = f.split()
        fname = parts[-1]
        if fname.endswith('.xml.gz') and not fname.endswith('.md5'):
            xml_files.append(fname)
    
    try:
        ftp.quit()
    except:
        pass
    
    return sorted(xml_files)

def get_new_files(all_files, state):
    """Get files not yet processed"""
    processed = set(state.get('files_processed', []))
    last_file = state.get('last_file')
    
    if last_file:
        # Get files after last processed
        new = [f for f in all_files if f > last_file]
    else:
        # First run - process all
        new = all_files
    
    # Skip already processed
    new = [f for f in new if f not in processed]
    return new

def download_file(fname):
    """Download update file"""
    url = f"https://ftp.ncbi.nlm.nih.gov/pubmed/updatefiles/{fname}"
    path = DOWNLOAD_DIR / fname
    
    if path.exists():
        return path
    
    print(f"Downloading {fname}...")
    r = requests.get(url, stream=True, timeout=60)
    with open(path, 'wb') as f:
        for chunk in r.iter_content(8192):
            f.write(chunk)
    return path

def parse_xml(xml_gz_path):
    """Parse PubMed XML file"""
    abstracts = []
    try:
        with gzip.open(xml_gz_path, 'rb') as f:
            tree = ET.parse(f)
            root = tree.getroot()
        
        for article in root.findall('.//PubmedArticle'):
            try:
                pmid = article.find('.//PMID').text
                title_el = article.find('.//ArticleTitle')
                title = title_el.text if title_el is not None else ""
                abstract_texts = article.findall('.//AbstractText')
                abstract = " ".join([
                    t.text for t in abstract_texts if t.text
                ])
                year_el = article.find('.//PubDate/Year')
                year = year_el.text if year_el is not None else ""
                mesh_terms = [
                    m.find('DescriptorName').text
                    for m in article.findall('.//MeshHeading')
                    if m.find('DescriptorName') is not None
                ]
                if abstract:
                    abstracts.append({
                        'pmid': pmid,
                        'title': title,
                        'abstract': abstract,
                        'year': year,
                        'mesh_terms': mesh_terms,
                        'last_updated': datetime.now().isoformat()
                    })
            except:
                continue
    except Exception as e:
        print(f"Error parsing {xml_gz_path}: {e}")
    
    return abstracts

def update_abstracts(abstracts):
    """Update existing abstracts in domain folders"""
    updated = 0
    new_count = 0
    
    for abstract in abstracts:
        pmid = abstract['pmid']
        saved = False
        
        for domain_dir in DATA_DIR.glob('*/'):
            abstract_file = domain_dir / f"{pmid}.json"
            if abstract_file.exists():
                with open(abstract_file, 'w') as f:
                    json.dump(abstract, f)
                updated += 1
                saved = True
                break
        
        if not saved:
            new_count += 1
    
    return updated, new_count

def main():
    print(f"🔄 PubMed Sync Pipeline")
    print(f"Started: {datetime.now().isoformat()}")
    
    # Load state
    state = load_state()
    print(f"Last sync: {state.get('last_sync', 'Never')}")
    print(f"Last file: {state.get('last_file', 'None')}")
    
    # Get new files
    print("\nFetching file list from PubMed FTP...")
    all_files = get_all_update_files()
    new_files = get_new_files(all_files, state)
    
    print(f"Total files on FTP: {len(all_files)}")
    print(f"New files to process: {len(new_files)}")
    
    if not new_files:
        print("✅ Already up to date!")
        return
    
    total_updated = state.get('total_updated', 0)
    total_new = state.get('total_new', 0)
    processed = state.get('files_processed', [])
    
    for i, fname in enumerate(new_files):
        print(f"\n[{i+1}/{len(new_files)}] {fname}")
        
        try:
            # Download
            path = download_file(fname)
            
            # Parse
            abstracts = parse_xml(path)
            print(f"Abstracts found: {len(abstracts)}")
            
            # Update
            updated, new_count = update_abstracts(abstracts)
            total_updated += updated
            total_new += new_count
            print(f"Updated: {updated} | New: {new_count}")
            
            # Mark as processed
            processed.append(fname)
            
            # Save state after each file
            state = {
                "last_file": fname,
                "last_sync": datetime.now().isoformat(),
                "total_updated": total_updated,
                "total_new": total_new,
                "files_processed": processed
            }
            save_state(state)
            
            # Cleanup
            path.unlink()
            
        except Exception as e:
            print(f"❌ Error processing {fname}: {e}")
            continue
    
    print(f"\n✅ Sync complete!")
    print(f"Files processed: {len(new_files)}")
    print(f"Total updated: {total_updated}")
    print(f"Total new: {total_new}")
    print(f"Finished: {datetime.now().isoformat()}")

if __name__ == "__main__":
    main()
