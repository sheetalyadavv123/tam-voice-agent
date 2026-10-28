"""Prints each scripted call and whether it passed. Add -v to see every step."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from tools.scenario_runner import load_scenarios, run_scenario

scenarios = load_scenarios(Path(__file__).parent.parent / "tests" / "scenarios.yaml")
failed = 0
for sc in scenarios:
    result = run_scenario(sc)
    print(("PASS  " if not result["failures"] else "FAIL  ") + sc["name"])
    if "-v" in sys.argv or result["failures"]:
        print("\n".join(result["log"]))
        for problem in result["failures"]:
            print("   !!", problem)
    failed += bool(result["failures"])
print(f"\n{len(scenarios) - failed}/{len(scenarios)} scenarios passed")
sys.exit(1 if failed else 0)