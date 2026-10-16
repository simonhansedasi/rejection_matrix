from pipeline import fails_description_gate as g, filter_results

def test_gates():
    assert g("Requires 7+ years of experience in data science")
    assert g("Currently pursuing a Doctorate Degree")
    assert g("2 years of experience in job offered, Operations Research")
    assert g("Must be graduating between Fall 2027 & Summer 2028")
    assert not g("1-3 years of experience with Python")
    assert not g("Our company has 10 years in business. 2 years experience preferred")
    assert not g("")

def test_titles():
    mk = lambda t: {'title': t, 'company': 'x', 'source': 'y', 'description': ''}
    kept = [j['title'] for j in filter_results([mk('GIS Data Analyst'), mk('Data Scientist Intern'),
            mk('PhD Data Science Internship'), mk('International Data Analyst')])]
    assert kept == ['GIS Data Analyst', 'International Data Analyst'], kept
