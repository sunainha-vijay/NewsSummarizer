    

Challenge 2: AI News Summarizer
-------------------------------

Demo Link: http://challenge2sunainha.s3-website.ap-south-1.amazonaws.com/

*   A serverless application to summarize news articles using an **AI API** and **DynamoDB** for caching.The project consists of:
    
*   **Frontend**: Static HTML (deployed on AWS S3)
    
*   **Backend**: Python Lambda (deployed via Serverless Framework)
    
*   **Database**: AWS DynamoDB (for caching results)
    

Features
--------

*   **AI-Powered Summarization** using the Hugging Face API
    
*   **Serverless Architecture** with AWS Lambda and API Gateway
    
*   **Performance Caching** with DynamoDB to reduce API calls
    
*   **Infrastructure as Code (IaC)** for automated backend deployment
    

Setup Instructions
------------------

### 1\. Prerequisites

1.  Install and configure the **AWS CLI**.
    
2.  Install **Node.js** and **npm**.
    
3.  Create a free account on **Hugging Face** and generate an Access Token.
    

### 2\. Backend Deployment

1.  Clone this repository.
    
2.  Install the Serverless plugin: npm install --save-dev serverless-python-requirements
    
3.  Set your Hugging Face token as an environment variable in PowerShell:$env:HUGGINGFACE\_API\_TOKEN='your\_token\_here'
    
4.  Deploy the backend using the Serverless Framework:serverless deploy
    
5.  Copy the **API endpoint URL** from the output.
    

### 3\. Frontend Deployment

1.  Create a new **S3 bucket**.
    
2.  Enable **Static website hosting** in the bucket properties.
    
3.  Apply a **public read bucket policy**.
    
4.  Open index.html, paste the copied API endpoint, and upload it to the S3 bucket.
    

Bonus Features & Automation
---------------------------

*   **IaC with Serverless Framework**: The entire backend infrastructure (Lambda, API Gateway, DynamoDB, IAM roles) is defined in serverless.yml and deployed automatically.
    
*   **DynamoDB Caching**:
    
    *   **MD5 Hash Keys**: URLs are hashed to create unique, deterministic keys for caching.
        
    *   **Cache Hits**: If a key exists, the cached summary is returned instantly.
        
    *   **Cache Misses**: If a key doesn't exist, a new summary is generated and stored in DynamoDB.
        
    *   **Auto-Expiration**: A **TTL (Time To Live)** attribute automatically deletes cached items after 7 days to keep content fresh.
        

Challenges & Assumptions
------------------------

*   **Local Permissions**: Required running PowerShell as an administrator to resolve npm permission errors during plugin installation.
    
*   **API Gateway CORS**: Fixed a "Failed to fetch" error by explicitly adding an OPTIONS method handler in serverless.yml to satisfy browser security checks.
    
*   **Assumption**: A cached summary is considered valid for retrieval for 24 hours, balancing performance with content freshness.
