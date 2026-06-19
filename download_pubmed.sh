docker exec omnibioai-studio-rag-1 bash -c "
export NCBI_EMAIL=mandecent.gupta@gmail.com
export RAG_DATA_DIR=/workspace/data/PubMed
export FAISS_INDEX_DIR=/workspace/data/Index

python -m ragbio.utils.rag_data_loader \
  --study Parkinson_CaseStudy \
  --search 'Parkinson Disease AND therapy AND neurodegeneration' \
  --retmax 500
"

# 2
docker exec omnibioai-studio-rag-1 bash -c "
export NCBI_EMAIL=mandecent.gupta@gmail.com
export RAG_DATA_DIR=/workspace/data/PubMed
export FAISS_INDEX_DIR=/workspace/data/Index
python -m ragbio.utils.rag_data_loader --study CRISPR_GenomeEditing --search 'CRISPR Cas9 AND gene editing AND therapeutic' --retmax 500
"

# 3
docker exec omnibioai-studio-rag-1 bash -c "
export NCBI_EMAIL=mandecent.gupta@gmail.com
export RAG_DATA_DIR=/workspace/data/PubMed
export FAISS_INDEX_DIR=/workspace/data/Index
python -m ragbio.utils.rag_data_loader --study Immunotherapy_Cancer --search 'cancer immunotherapy AND checkpoint inhibitor AND PD-1' --retmax 500
"

# 4
docker exec omnibioai-studio-rag-1 bash -c "
export NCBI_EMAIL=mandecent.gupta@gmail.com
export RAG_DATA_DIR=/workspace/data/PubMed
export FAISS_INDEX_DIR=/workspace/data/Index
python -m ragbio.utils.rag_data_loader --study Cardiovascular_Disease --search 'cardiovascular disease AND heart failure AND biomarker' --retmax 500
"

# 5
docker exec omnibioai-studio-rag-1 bash -c "
export NCBI_EMAIL=mandecent.gupta@gmail.com
export RAG_DATA_DIR=/workspace/data/PubMed
export FAISS_INDEX_DIR=/workspace/data/Index
python -m ragbio.utils.rag_data_loader --study SingleCell_RNAseq --search 'single cell RNA sequencing AND cell type AND transcriptomics' --retmax 500
"
