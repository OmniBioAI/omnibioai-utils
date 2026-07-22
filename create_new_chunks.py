#!/usr/bin/env python3
"""
Create new corpus chunks from updated PubMed abstracts
Runs after sync_pubmed_updates.py completes
"""
import json
import os
from pathlib import Path
from datetime import datetime

DATA_DIR = Path("/home/manish/Desktop/machine/data/PubMed/Abstracts")
STATE_FILE = Path("/home/manish/Desktop/machine/data/PubMed/sync_state.json")
CHUNK_SIZE = 500000

def get_updated_pmids():
    """Get PMIDs that were updated during sync"""
    print("Finding updated abstracts...")
    
    updated = []
    
    # Check modification time - files updated after May 31
    cutoff = datetime(2026, 6, 1).timestamp()
    
    for domain_dir in DATA_DIR.glob('*/'):
        for abstract_file in domain_dir.glob('*.json'):
            if abstract_file.stat().st_mtime > cutoff:
                updated.append(abstract_file)
    
    print(f"Updated files: {len(updated)}")
    return updated

def find_new_abstracts():
    """Find abstracts that are new (from sync) but not in any domain"""
    print("Finding new abstracts not in any domain...")
    
    # Load sync state to get new PMIDs
    with open(STATE_FILE) as f:
        state = json.load(f)
    
    print(f"Total new from sync: {state.get('total_new', 0)}")
    return state

def get_next_chunk_number():
    """Get next available chunk number"""
    existing = list(DATA_DIR.glob('_general_corpus_chunk*'))
    if not existing:
        return 57
    numbers = []
    for d in existing:
        try:
            num = int(d.name.replace('_general_corpus_chunk', ''))
            numbers.append(num)
        except:
            continue
    return max(numbers) + 1 if numbers else 57

def create_chunks_from_updates():
    """Create new chunks from updated abstracts"""
    
    print("=== Creating New Chunks from PubMed Updates ===")
    print(f"Started: {datetime.now()}")
    
    # Collect all updated abstracts
    updated_files = get_updated_pmids()
    print(f"\nTotal updated abstracts: {len(updated_files)}")
    
    if not updated_files:
        print("No updates found!")
        return
    
    # Get next chunk number
    next_chunk = get_next_chunk_number()
    print(f"Starting chunk number: {next_chunk}")
    
    # Create chunks
    chunk_num = next_chunk
    current_chunk = []
    chunks_created = []
    
    for i, abstract_file in enumerate(updated_files):
        try:
            with open(abstract_file) as f:
                abstract = json.load(f)
            current_chunk.append(abstract)
            
            if len(current_chunk) >= CHUNK_SIZE:
                # Save chunk
                chunk_name = f"_general_corpus_chunk{chunk_num:03d}"
                chunk_dir = DATA_DIR / chunk_name
                chunk_dir.mkdir(exist_ok=True)
                
                for abstract in current_chunk:
                    pmid = abstract.get('pmid', str(i))
                    with open(chunk_dir / f"{pmid}.json", 'w') as f:
                        json.dump(abstract, f)
                
                print(f"✅ Created {chunk_name}: {len(current_chunk)} abstracts")
                chunks_created.append(chunk_name)
                chunk_num += 1
                current_chunk = []
            
            if i % 10000 == 0:
                print(f"Progress: {i}/{len(updated_files)}")
                
        except Exception as e:
            continue
    
    # Save remaining
    if current_chunk:
        chunk_name = f"_general_corpus_chunk{chunk_num:03d}"
        chunk_dir = DATA_DIR / chunk_name
        chunk_dir.mkdir(exist_ok=True)
        
        for abstract in current_chunk:
            pmid = abstract.get('pmid', 'unknown')
            with open(chunk_dir / f"{pmid}.json", 'w') as f:
                json.dump(abstract, f)
        
        print(f"✅ Created {chunk_name}: {len(current_chunk)} abstracts")
        chunks_created.append(chunk_name)
    
    print(f"\n🎉 Done!")
    print(f"Chunks created: {len(chunks_created)}")
    for c in chunks_created:
        print(f"  ✅ {c}")
    
    print(f"\nNext steps:")
    print(f"1. Embed chunks on MacBook/DGX")
    print(f"2. Build FAISS indexes")
    print(f"3. Upload to HuggingFace")

if __name__ == "__main__":
    create_chunks_from_updates()
