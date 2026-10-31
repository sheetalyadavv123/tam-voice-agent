import pytest

from tools import caller_words, names
from tools.store import load_company_data

# Spellings speech-to-text may give for each first name in the mock data
SPELLINGS = {
    "Rahul": ["Rahul", "राहुल", "Rahool"],
    "Sunita": ["Sunita", "सुनीता", "सुनिता", "सनीता", "Suneeta", "Sunitha"],
    "Amit": ["Amit", "अमित", "Ameet"],
    "Priya": ["Priya", "प्रिया", "Priyaa", "Pria"],
    "Subramaniam": ["Subramaniam", "सुब्रमण्यम", "Subramanyam", "सुब्रमणियम"],
    "कविता": ["कविता", "Kavita", "Kavitha", "कवीता"],
    "Mohammed": ["Mohammed", "मोहम्मद", "Mohammad", "मोहमद"],
    "Neha": ["Neha", "नेहा", "Nehaa"],
    "Deepak": ["Deepak", "दीपक", "Dipak"],
    "Anil": ["Anil", "अनिल", "Aneel"],
}
NOT_NAMES = ["nahi", "haan", "raha", "rahi", "abhi", "bhai", "behen", "suniye", "bolo", "unki",
             "Sunil", "Anita", "Rohit", "Pooja", "Ravi", "Kavya"]


def test_every_mock_customer_is_covered():
    customers, _ = load_company_data()
    assert {c["name"].split()[0] for c in customers} == set(SPELLINGS)


@pytest.mark.parametrize("name,spelling", [(n, s) for n, ss in SPELLINGS.items() for s in ss])
def test_spellings_match(name, spelling):
    assert names.same_name(spelling, name)


@pytest.mark.parametrize("name", SPELLINGS)
def test_common_words_and_other_names_do_not_match(name):
    assert [w for w in NOT_NAMES if names.same_name(w, name)] == []


@pytest.mark.parametrize("name,spelling", [(n, ss[1]) for n, ss in SPELLINGS.items()])
def test_the_name_in_another_script_confirms_identity(name, spelling):
    assert caller_words.confirms_identity(f"{spelling} बोल रही हूँ", name)
    assert caller_words.confirms_identity(f"ji, {spelling}", name)


def test_the_name_said_as_absent_is_not_a_yes():
    assert not caller_words.confirms_identity("Sunita ghar pe nahi hai", "Sunita")
    assert caller_words.someone_else("Sunita ghar pe nahi hai", "Sunita")
