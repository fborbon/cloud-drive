from setuptools import setup, find_packages

setup(
    name="cloud-drive",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "boto3>=1.34.0",
        "click>=8.1.0",
        "rich>=13.7.0",
        "pyyaml>=6.0.1",
        "python-dotenv>=1.0.0",
        "tqdm>=4.66.0",
    ],
    entry_points={
        "console_scripts": [
            "cloud-drive=cloud_drive.cli:cli",
        ],
    },
    python_requires=">=3.11",
)
