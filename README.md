# recruitment-system-back

 This is a system that automates the full recruitment process, including job posting, candidate sourcing, CV parsing, screening, and ranking. It also supports technical and HR interview assessments. The system uses AI to make hiring faster, reduce bias, and improve matching between candidates and job requirements.

## Requirements

### Python Version

Python 3.13 or later is required.

### Install python using Mini-conda

#### Step 1: Download and install MiniConda

Download and install MiniConda from [MiniConda installation guide](https://www.anaconda.com/docs/getting-started/miniconda/install#windows-installation)

#### Step 2: Create new environment

Create a new environment using the following command:

```bash
conda create -n recruitment-system-back python=3.13
```

1) Activate the environment:

```bash
conda activate recruitment-system-back
```

### (Optional) Setup your command line interface for better readability

```bash
PROMPT %USERNAME%@%COMPUTERNAME%:$P$_$
```

#### and to retun back to the default

```bash
PROMPT $P$G
```

## Installation

### Install Requirements

```bash
pip install -r requirements.txt
```

### setup the environment variables

```bash
copy .env.example .env
```

set your envvironment variables in the .env file.

## Run the fastAPI server

```bash
uvicorn main:app --reload 
```
