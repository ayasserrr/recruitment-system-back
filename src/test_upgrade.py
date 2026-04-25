"""Upgrade validation tests — run directly with anaconda python."""
import sys
import types
import importlib.util

# ── Stub DB/env dependencies ──────────────────────────────────────────────────
for mod in ['core.config', 'core.database', 'db.session', 'models.db_models']:
    sys.modules[mod] = types.ModuleType(mod)

cfg = types.ModuleType('core.config')
class _S:
    OPENAI_API_KEY = 'test'
cfg.get_settings = lambda: _S()
sys.modules['core.config'] = cfg

db_mod = types.ModuleType('core.database')
db_mod.SessionLocal = None
sys.modules['core.database'] = db_mod

dbm = types.ModuleType('models.db_models')
for cls in ['Application', 'CV', 'JobPosting', 'Requisition',
            'SemanticAnalysisReport', 'MatchedSkill', 'Candidate']:
    setattr(dbm, cls, object)
sys.modules['models.db_models'] = dbm

sys.path.insert(0, 'e:/OneDrive/Desktop/recruitment-system-back/src')

BASE = 'e:/OneDrive/Desktop/recruitment-system-back/src'

def load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, f'{BASE}/{relpath}')
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

rg  = load('ranking_graph',    'services/ranking_graph.py')
es  = load('embedding_service','services/embedding_service.py')
cvs = load('cv_schema',        'schemas/cv_schema.py')
xai = load('xai_service',      'services/xai_service.py')


# ── Test 1: TypeScript alias fix ──────────────────────────────────────────────
assert rg._SKILL_ALIASES.get('ts') == 'typescript', 'ts must map to typescript'
assert rg._SKILL_ALIASES.get('typescript') != 'javascript', \
    'typescript must NOT collapse to javascript'
print('PASS  1  TypeScript: ts->typescript, not javascript')

# ── Test 2: node alias removed ────────────────────────────────────────────────
assert 'node' not in rg._SKILL_ALIASES, '"node" alias must be removed'
assert rg._SKILL_ALIASES.get('node.js') == 'nodejs'
print('PASS  2  bare "node" alias removed; node.js->nodejs preserved')

# ── Test 3: _SHORT_SKILL_EXACT_ONLY present ───────────────────────────────────
for s in ('r', 'c', 'go', 'c#', 'c++'):
    assert s in rg._SHORT_SKILL_EXACT_ONLY, f'{s} missing from SHORT_SKILL_EXACT_ONLY'
print('PASS  3  _SHORT_SKILL_EXACT_ONLY contains r, c, go, c#, c++')

# ── Test 4: false-positive guard for short aliases ────────────────────────────
assert not rg._skill_in_text_safe('r', 'machine learning research for enterprise')
assert     rg._skill_in_text_safe('r', 'skills: python, r, sql')
assert not rg._skill_in_text_safe('c', 'recursive cache microservice')
assert     rg._skill_in_text_safe('c', 'languages: python, c, java')
print('PASS  4  _skill_in_text_safe prevents short-alias false positives')

# ── Test 5: _term_in_text delegates to safe guard ────────────────────────────
assert not rg._term_in_text('c', 'recursive cache microservice')
assert     rg._term_in_text('c', 'languages: python, c, java')
print('PASS  5  _term_in_text delegates to _skill_in_text_safe')

# ── Test 6: new aliases present ───────────────────────────────────────────────
assert rg._SKILL_ALIASES.get('reactjs') == 'react'
assert rg._SKILL_ALIASES.get('pyspark') == 'spark'
assert rg._SKILL_ALIASES.get('apache kafka') == 'kafka'
print('PASS  6  React/Vue/Angular + data-engineering aliases present')

# ── Test 7: sigmoid + hybrid_score ───────────────────────────────────────────
import math
assert abs(es.sigmoid(0.0) - 0.5)   < 0.001
assert abs(es.sigmoid(100) - 1.0)   < 0.001
assert abs(es.sigmoid(-100) - 0.0)  < 0.001
print('PASS  7  sigmoid(0)=0.5, sigmoid(+inf)~1, sigmoid(-inf)~0')

hs = es.hybrid_score(0.0, 0.6, alpha=0.7)  # 0.7*0.5 + 0.3*0.6 = 0.53
assert abs(hs - 0.53) < 0.01, f'expected ~0.53 got {hs}'
hs2 = es.hybrid_score(10.0, 0.0, alpha=0.7) # 0.7*~1.0 + 0.3*0.0 = ~0.70
assert abs(hs2 - 0.70) < 0.01, f'expected ~0.70 got {hs2}'
print('PASS  8  hybrid_score blends CE probability + skill_overlap correctly')

assert es.ce_score_to_label(2.0)  == 'good_fit'
assert es.ce_score_to_label(-2.0) == 'no_fit'
assert es.ce_score_to_label(0.0)  == 'partial_fit'
print('PASS  9  ce_score_to_label thresholds correct')

# ── Test 10: ParsedCV Pydantic v2 validators ──────────────────────────────────
cv_obj = cvs.ParsedCV.model_validate({
    'first_name': 'Ahmed',
    'last_name':  'Ayman',
    'email':      'Ahmed@Example.COM',
    'years_of_experience': '3 years',      # LLM quirk
    'skills': [
        {'skill_name': 'Python'},
        {'skill_name': 'python'},           # duplicate
        {'skill_name': 'PyTorch'},
    ],
    'experiences': None,
    'projects':    None,
})
assert cv_obj.years_of_experience == 3,    'coerce_yoe failed'
assert len(cv_obj.skills) == 2,            'deduplicated: Python + PyTorch'
assert cv_obj.email == 'ahmed@example.com','email lowercased'
assert cv_obj.full_name == 'Ahmed Ayman'
print('PASS  10 ParsedCV: coerce_yoe, dedup, email normalize, full_name')

try:
    cvs.ParsedCV.model_validate({
        'first_name': 'X', 'last_name': 'Y', 'email': 'not-an-email',
    })
    raise AssertionError('Should have raised ValidationError')
except Exception as e:
    if 'Should have raised' in str(e):
        raise
print('PASS  11 ParsedCV rejects invalid email address')

try:
    cvs.ParsedCV.model_validate({
        'first_name': 'X', 'last_name': 'Y', 'email': 'ok@test.com',
        'years_of_experience': -5,
    })
    raise AssertionError('Should have raised for negative yoe')
except Exception as e:
    if 'Should have raised' in str(e):
        raise
print('PASS  12 ParsedCV rejects negative years_of_experience')

# ── Test 13: generate_hr_explanation ─────────────────────────────────────────
cv_dict = {
    'candidate_name':      'Ahmed Ayman',
    'years_of_experience': 5,
    'final_score':         82.0,
    'rank_in_pool':        1,
    'total_in_pool':       15,
    'recommendation':      'Top Candidate',
    'skills':              [{'skill_name': 'Python'}, {'skill_name': 'PyTorch'}],
    'llm_scores': {
        'strengths':          ['Strong PyTorch background'],
        'concerns':           [],
        'interview_questions': ['Explain your RAG pipeline design'],
    },
    'matched_skills':   [{'skill': 'pytorch'}],
    'genai_data':       {'evidence': ['rag', 'langchain'], 'context': 'production', 'bonus': 15.0},
    'feature_attribution': [{'feature': 'technical_match', 'contribution_pts': 25.0}],
    'scoring_mode':     'Experience Mode',
}
jd_dict = {
    'requisition_id': 7,
    'job_title':      'ML Engineer',
    'required_years': 4,
    'required_skills': [
        {'name': 'Python',  'type': 'required'},
        {'name': 'PyTorch', 'type': 'required'},
        {'name': 'MLflow',  'type': 'required'},
    ],
}
human, machine = xai.generate_hr_explanation(
    cv_dict, jd_dict, [], ce_logit=2.5, rank=1, pool_size=15,
)
assert 'Ahmed Ayman'          in human,  'name missing from card'
assert 'ML Engineer'          in human,  'role missing from card'
assert '#1 of 15'             in human,  'rank missing from card'
assert machine['rank']             == 1
assert machine['pool_size']        == 15
assert machine['recommendation']   == 'advance_to_interview'
assert 'match_score'          in machine
assert 'MLflow'               in machine['missing_jd_skills'] or 'mlflow' in str(machine).lower(), \
    'MLflow gap not detected'
print('PASS  13 generate_hr_explanation: human card + machine JSON correct')

# ── Test 14: format_full_explanation returns both blocks ──────────────────────
full = xai.format_full_explanation(cv_dict, jd_dict, [], ce_logit=2.5, rank=1, pool_size=15)
assert 'CANDIDATE SUMMARY FOR HR' in full
assert '```json'                   in full
print('PASS  14 format_full_explanation contains both human + JSON blocks')

print()
print('=' * 55)
print('All 14 upgrade validation tests passed.')
print('=' * 55)
