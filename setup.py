from setuptools import setup

setup(
    name="git-mediate",
    version="0.1.0",
    description="A Git extension to identify the source of merge conflicts",
    author="Will Regelmann",
    author_email="will@regelmann.net",
    license="MIT",
    url="https://github.com/willregelmann/git-mediate",
    py_modules=["git_mediate"],
    entry_points={
        'console_scripts': [
            'git-mediate=git_mediate:main',
        ],
    },
    python_requires=">=3.6",
)
