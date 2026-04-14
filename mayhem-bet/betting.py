"""
내기 정산 로직
- 매 판 딜량 순위 → 순위별 금액 이동
- 다판 누적 → 최종 정산
"""


def calculate_match_transfers(results, rules):
    """
    한 판의 딜량 결과와 규칙으로 금액 이동을 계산한다.
    
    Args:
        results: [{"name": str, "champion": str, "damage": int}, ...] (딜량 내림차순)
        rules: [{"from_rank": int, "to_rank": int, "amount": int}, ...]
    
    Returns:
        list of dict: [{"from": str, "to": str, "amount": int}, ...]
    """
    transfers = []
    for rule in rules:
        from_idx = rule["from_rank"] - 1  # 1-indexed → 0-indexed
        to_idx = rule["to_rank"] - 1

        if from_idx < len(results) and to_idx < len(results):
            transfers.append({
                "from": results[from_idx]["name"],
                "to": results[to_idx]["name"],
                "amount": rule["amount"],
            })
    return transfers


def calculate_settlement(matches, rules):
    """
    여러 판의 결과를 누적하여 최종 정산 금액을 계산한다.
    
    Args:
        matches: [{"results": [{"name", "champion", "damage"}, ...]}, ...]
        rules: [{"from_rank": int, "to_rank": int, "amount": int}, ...]
    
    Returns:
        dict: {
            "per_match": [{"match_number", "transfers": [...], "rankings": [...]}, ...],
            "totals": {"소환사명": +/-금액, ...},
            "final_transfers": [{"from": str, "to": str, "amount": int}, ...]
        }
    """
    totals = {}  # 소환사명 → 누적 금액 (+받을돈, -낼돈)
    per_match = []

    for match in matches:
        results = match["results"]
        transfers = calculate_match_transfers(results, rules)

        # 누적 계산
        for t in transfers:
            totals[t["from"]] = totals.get(t["from"], 0) - t["amount"]
            totals[t["to"]] = totals.get(t["to"], 0) + t["amount"]

        per_match.append({
            "match_number": match.get("match_number", len(per_match) + 1),
            "rankings": [
                {"rank": i + 1, "name": r["name"], "champion": r["champion"], "damage": r["damage"]}
                for i, r in enumerate(results)
            ],
            "transfers": transfers,
        })

    # 최종 정산: 빚진 사람 → 받을 사람으로 간소화
    final_transfers = simplify_debts(totals)

    return {
        "per_match": per_match,
        "totals": totals,
        "final_transfers": final_transfers,
    }


def simplify_debts(totals):
    """
    최종 잔액을 기반으로 최소 거래 횟수로 정산을 간소화한다.
    
    예: A: -3000, B: +1000, C: +2000
    → A가 B에게 1000, A가 C에게 2000
    """
    # 빚진 사람(음수)과 받을 사람(양수) 분리
    debtors = []  # (이름, 금액) - 금액은 양수로 변환
    creditors = []  # (이름, 금액)

    for name, amount in totals.items():
        if amount < 0:
            debtors.append([name, -amount])
        elif amount > 0:
            creditors.append([name, amount])

    # 큰 금액부터 정산
    debtors.sort(key=lambda x: x[1], reverse=True)
    creditors.sort(key=lambda x: x[1], reverse=True)

    transfers = []
    i, j = 0, 0
    while i < len(debtors) and j < len(creditors):
        debtor_name, debt = debtors[i]
        creditor_name, credit = creditors[j]

        settle = min(debt, credit)
        if settle > 0:
            transfers.append({
                "from": debtor_name,
                "to": creditor_name,
                "amount": settle,
            })

        debtors[i][1] -= settle
        creditors[j][1] -= settle

        if debtors[i][1] == 0:
            i += 1
        if creditors[j][1] == 0:
            j += 1

    return transfers
