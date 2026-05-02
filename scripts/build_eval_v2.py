"""Build expanded v2 eval sets for QA and research.

Reads the locked v1 eval files (15 tasks each), appends 35 new tasks per
domain following the same schemas, and writes:

  - evals/qa/eval_v2_set.json        (50 tasks)
  - evals/research/eval_v2_set.json  (50 tasks)

Before writing the QA file, every (buggy, fixed) pair is sanity-checked
by running pytest twice in a tmpdir against a single trivial test that
just imports the function and confirms it's callable. The real per-task
test files come from the agent at eval time; what we verify here is only
that the buggy and fixed implementations parse and run.

Run:
    python -m scripts.build_eval_v2
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


# --------------------------------------------------------------------- QA new tasks


# 35 new QA seeded-bug tasks. Each follows the eval_set.json schema:
# {id, spec, function_name, module_name, buggy_code, fixed_code, bug_hint}
NEW_QA_TASKS: list[dict] = [
    {
        "id": "qa_eval_16_factorial",
        "spec": "factorial(n: int) -> int returns n! for n>=0. factorial(0) == 1.",
        "function_name": "factorial",
        "module_name": "target",
        "buggy_code": "def factorial(n):\n    result = 1\n    for i in range(1, n):\n        result *= i\n    return result\n",
        "fixed_code": "def factorial(n):\n    result = 1\n    for i in range(1, n + 1):\n        result *= i\n    return result\n",
        "bug_hint": "off-by-one: range(1, n) excludes n itself",
    },
    {
        "id": "qa_eval_17_gcd",
        "spec": "gcd(a: int, b: int) -> int returns the greatest common divisor of a and b (assume a>=0, b>=0, not both zero).",
        "function_name": "gcd",
        "module_name": "target",
        "buggy_code": "def gcd(a, b):\n    while b:\n        a = b\n        b = a % b\n    return a\n",
        "fixed_code": "def gcd(a, b):\n    while b:\n        a, b = b, a % b\n    return a\n",
        "bug_hint": "non-simultaneous assignment clobbers a before computing a % b",
    },
    {
        "id": "qa_eval_18_reverse_string",
        "spec": "reverse(s: str) -> str returns s reversed character-by-character.",
        "function_name": "reverse",
        "module_name": "target",
        "buggy_code": "def reverse(s):\n    return s[::1]\n",
        "fixed_code": "def reverse(s):\n    return s[::-1]\n",
        "bug_hint": "step is +1 instead of -1; returns input unchanged",
    },
    {
        "id": "qa_eval_19_max_subarray_sum",
        "spec": "max_subarray_sum(nums: list[int]) -> int returns the maximum sum of any contiguous non-empty subarray. For input [-2,1,-3,4,-1,2,1,-5,4] returns 6.",
        "function_name": "max_subarray_sum",
        "module_name": "target",
        "buggy_code": "def max_subarray_sum(nums):\n    best = 0\n    cur = 0\n    for x in nums:\n        cur = max(x, cur + x)\n        best = max(best, cur)\n    return best\n",
        "fixed_code": "def max_subarray_sum(nums):\n    best = nums[0]\n    cur = nums[0]\n    for x in nums[1:]:\n        cur = max(x, cur + x)\n        best = max(best, cur)\n    return best\n",
        "bug_hint": "starts best=0 so all-negative arrays return 0 instead of the largest (least-negative) element",
    },
    {
        "id": "qa_eval_20_count_substring",
        "spec": "count_sub(s: str, sub: str) -> int returns the number of non-overlapping occurrences of sub in s. count_sub('aaaa','aa') == 2.",
        "function_name": "count_sub",
        "module_name": "target",
        "buggy_code": "def count_sub(s, sub):\n    count = 0\n    for i in range(len(s)):\n        if s[i:i + len(sub)] == sub:\n            count += 1\n    return count\n",
        "fixed_code": "def count_sub(s, sub):\n    if not sub:\n        return 0\n    count = 0\n    i = 0\n    while i <= len(s) - len(sub):\n        if s[i:i + len(sub)] == sub:\n            count += 1\n            i += len(sub)\n        else:\n            i += 1\n    return count\n",
        "bug_hint": "counts overlapping occurrences; 'aaaa' with 'aa' returns 3 instead of 2",
    },
    {
        "id": "qa_eval_21_remove_duplicates_sorted",
        "spec": "dedupe_sorted(arr: list[int]) -> list[int] takes a sorted ascending list and returns the same elements with consecutive duplicates removed, order preserved.",
        "function_name": "dedupe_sorted",
        "module_name": "target",
        "buggy_code": "def dedupe_sorted(arr):\n    out = []\n    for i in range(len(arr) - 1):\n        if arr[i] != arr[i + 1]:\n            out.append(arr[i])\n    return out\n",
        "fixed_code": "def dedupe_sorted(arr):\n    if not arr:\n        return []\n    out = [arr[0]]\n    for i in range(1, len(arr)):\n        if arr[i] != arr[i - 1]:\n            out.append(arr[i])\n    return out\n",
        "bug_hint": "drops the last element entirely",
    },
    {
        "id": "qa_eval_22_safe_divide",
        "spec": "safe_divide(a: float, b: float) -> float returns a/b. If b is zero, returns float('inf') if a>0, float('-inf') if a<0, and 0.0 if a==0.",
        "function_name": "safe_divide",
        "module_name": "target",
        "buggy_code": "def safe_divide(a, b):\n    if b == 0:\n        return float('inf') if a > 0 else float('-inf')\n    return a / b\n",
        "fixed_code": "def safe_divide(a, b):\n    if b == 0:\n        if a > 0:\n            return float('inf')\n        if a < 0:\n            return float('-inf')\n        return 0.0\n    return a / b\n",
        "bug_hint": "0/0 returns -inf instead of 0.0",
    },
    {
        "id": "qa_eval_23_title_case",
        "spec": "title_case(s: str) -> str returns s with the first character of each whitespace-separated word capitalized and the rest lowercased. 'hELLO wORLD' -> 'Hello World'.",
        "function_name": "title_case",
        "module_name": "target",
        "buggy_code": "def title_case(s):\n    return ' '.join(w.capitalize() for w in s.split(' '))\n",
        "fixed_code": "def title_case(s):\n    return ' '.join(w.capitalize() for w in s.split())\n",
        "bug_hint": "split(' ') preserves runs of spaces as empty tokens",
    },
    {
        "id": "qa_eval_24_pairs_summing_to_k",
        "spec": "pair_count(nums: list[int], k: int) -> int returns the number of unique unordered pairs (i, j) with i<j such that nums[i] + nums[j] == k.",
        "function_name": "pair_count",
        "module_name": "target",
        "buggy_code": "def pair_count(nums, k):\n    count = 0\n    for i in range(len(nums)):\n        for j in range(len(nums)):\n            if i != j and nums[i] + nums[j] == k:\n                count += 1\n    return count\n",
        "fixed_code": "def pair_count(nums, k):\n    count = 0\n    for i in range(len(nums)):\n        for j in range(i + 1, len(nums)):\n            if nums[i] + nums[j] == k:\n                count += 1\n    return count\n",
        "bug_hint": "counts each unordered pair twice",
    },
    {
        "id": "qa_eval_25_running_average",
        "spec": "running_avg(nums: list[float]) -> list[float] returns a list whose ith element is the mean of nums[0..i] inclusive.",
        "function_name": "running_avg",
        "module_name": "target",
        "buggy_code": "def running_avg(nums):\n    out = []\n    total = 0\n    for i, x in enumerate(nums):\n        total += x\n        out.append(total / (i))\n    return out\n",
        "fixed_code": "def running_avg(nums):\n    out = []\n    total = 0\n    for i, x in enumerate(nums):\n        total += x\n        out.append(total / (i + 1))\n    return out\n",
        "bug_hint": "divides by i instead of i+1; first element triggers ZeroDivisionError",
    },
    {
        "id": "qa_eval_26_string_to_int_list",
        "spec": "to_int_list(s: str) -> list[int] takes a comma-separated string of integers (with optional whitespace) and returns the list of ints. Empty string returns [].",
        "function_name": "to_int_list",
        "module_name": "target",
        "buggy_code": "def to_int_list(s):\n    return [int(x) for x in s.split(',')]\n",
        "fixed_code": "def to_int_list(s):\n    if not s.strip():\n        return []\n    return [int(x.strip()) for x in s.split(',')]\n",
        "bug_hint": "empty string -> [int('')] which raises ValueError; whitespace around numbers also fails",
    },
    {
        "id": "qa_eval_27_chunk_list",
        "spec": "chunk(lst: list, n: int) -> list[list] splits lst into consecutive chunks of size n. The last chunk may be shorter if len(lst) is not a multiple of n. n>0.",
        "function_name": "chunk",
        "module_name": "target",
        "buggy_code": "def chunk(lst, n):\n    return [lst[i:i + n] for i in range(0, len(lst), n + 1)]\n",
        "fixed_code": "def chunk(lst, n):\n    return [lst[i:i + n] for i in range(0, len(lst), n)]\n",
        "bug_hint": "step is n+1 instead of n; loses one element between chunks",
    },
    {
        "id": "qa_eval_28_normalize_whitespace",
        "spec": "normalize(s: str) -> str collapses runs of whitespace (spaces, tabs, newlines) into single spaces and strips leading/trailing whitespace.",
        "function_name": "normalize",
        "module_name": "target",
        "buggy_code": "def normalize(s):\n    return s.replace('  ', ' ').strip()\n",
        "fixed_code": "def normalize(s):\n    return ' '.join(s.split())\n",
        "bug_hint": "only collapses double-spaces in one pass; '   ' becomes ' ', tabs/newlines unchanged",
    },
    {
        "id": "qa_eval_29_dot_product",
        "spec": "dot(a: list[float], b: list[float]) -> float returns the sum of element-wise products of two equal-length lists. Empty inputs return 0.0.",
        "function_name": "dot",
        "module_name": "target",
        "buggy_code": "def dot(a, b):\n    return sum(a[i] * b[i] for i in range(len(a)))\n",
        "fixed_code": "def dot(a, b):\n    if len(a) != len(b):\n        raise ValueError('length mismatch')\n    return sum(a[i] * b[i] for i in range(len(a)))\n",
        "bug_hint": "silently produces wrong answer when len(a) > len(b) (IndexError) or len(a) < len(b) (ignores trailing b)",
    },
    {
        "id": "qa_eval_30_count_set_bits",
        "spec": "popcount(n: int) -> int returns the number of 1 bits in the binary representation of n. n>=0.",
        "function_name": "popcount",
        "module_name": "target",
        "buggy_code": "def popcount(n):\n    count = 0\n    while n > 0:\n        if n // 2 == n:\n            count += 1\n        n //= 2\n    return count\n",
        "fixed_code": "def popcount(n):\n    count = 0\n    while n:\n        count += n & 1\n        n >>= 1\n    return count\n",
        "bug_hint": "uses `n // 2 == n` which is only true for n==0; effectively counts zero set bits for any positive input",
    },
    {
        "id": "qa_eval_31_camel_to_snake",
        "spec": "camel_to_snake(s: str) -> str converts CamelCase to snake_case. 'CamelCase' -> 'camel_case'. 'IOStream' -> 'io_stream'. 'simple' -> 'simple'.",
        "function_name": "camel_to_snake",
        "module_name": "target",
        "buggy_code": "def camel_to_snake(s):\n    out = []\n    for c in s:\n        if c.isupper():\n            out.append('_')\n            out.append(c.lower())\n        else:\n            out.append(c)\n    return ''.join(out).lstrip('_')\n",
        "fixed_code": "import re\n\ndef camel_to_snake(s):\n    s = re.sub(r'(.)([A-Z][a-z]+)', r'\\1_\\2', s)\n    s = re.sub(r'([a-z0-9])([A-Z])', r'\\1_\\2', s)\n    return s.lower()\n",
        "bug_hint": "'IOStream' becomes 'i_o_stream' instead of 'io_stream' (every uppercase gets a separator)",
    },
    {
        "id": "qa_eval_32_intersect_sorted",
        "spec": "intersect(a: list[int], b: list[int]) -> list[int] returns a sorted ascending list of integers present in BOTH inputs. Each value appears at most once even if duplicated in the inputs.",
        "function_name": "intersect",
        "module_name": "target",
        "buggy_code": "def intersect(a, b):\n    return sorted([x for x in a if x in b])\n",
        "fixed_code": "def intersect(a, b):\n    return sorted(set(a) & set(b))\n",
        "bug_hint": "preserves duplicates from a; intersect([1,1,2],[1,2,2]) returns [1,1,2] instead of [1,2]",
    },
    {
        "id": "qa_eval_33_shift_array",
        "spec": "shift_left(arr: list, k: int) -> list returns arr rotated left by k positions. k can be larger than len(arr); k can be negative (right rotation).",
        "function_name": "shift_left",
        "module_name": "target",
        "buggy_code": "def shift_left(arr, k):\n    return arr[k:] + arr[:k]\n",
        "fixed_code": "def shift_left(arr, k):\n    if not arr:\n        return []\n    k = k % len(arr)\n    return arr[k:] + arr[:k]\n",
        "bug_hint": "k > len(arr) drops elements; k < 0 produces wrong rotation; empty list crashes on modulo",
    },
    {
        "id": "qa_eval_34_pythag_triple",
        "spec": "is_pythag(a: int, b: int, c: int) -> bool returns True iff (a, b, c) form a Pythagorean triple in any order, i.e., the sum of squares of the two smallest equals the square of the largest, and all three are positive.",
        "function_name": "is_pythag",
        "module_name": "target",
        "buggy_code": "def is_pythag(a, b, c):\n    return a * a + b * b == c * c\n",
        "fixed_code": "def is_pythag(a, b, c):\n    if a <= 0 or b <= 0 or c <= 0:\n        return False\n    s = sorted([a, b, c])\n    return s[0] * s[0] + s[1] * s[1] == s[2] * s[2]\n",
        "bug_hint": "assumes c is the hypotenuse; (5,4,3) returns False even though it's a valid triple",
    },
    {
        "id": "qa_eval_35_word_freq",
        "spec": "word_freq(s: str) -> dict[str,int] returns a dict mapping lowercased whitespace-separated words to their occurrence counts. Empty string returns {}.",
        "function_name": "word_freq",
        "module_name": "target",
        "buggy_code": "def word_freq(s):\n    out = {}\n    for w in s.split(' '):\n        out[w] = out.get(w, 0) + 1\n    return out\n",
        "fixed_code": "def word_freq(s):\n    out = {}\n    for w in s.split():\n        out[w.lower()] = out.get(w.lower(), 0) + 1\n    return out\n",
        "bug_hint": "case-sensitive and uses split(' ') so multiple spaces produce empty-string keys",
    },
    {
        "id": "qa_eval_36_zip_unequal",
        "spec": "zip_short(a: list, b: list) -> list[tuple] returns a list of pairs (a[i], b[i]) up to the length of the shorter list. zip_short([1,2],[3,4,5]) == [(1,3),(2,4)].",
        "function_name": "zip_short",
        "module_name": "target",
        "buggy_code": "def zip_short(a, b):\n    n = max(len(a), len(b))\n    return [(a[i], b[i]) for i in range(n)]\n",
        "fixed_code": "def zip_short(a, b):\n    n = min(len(a), len(b))\n    return [(a[i], b[i]) for i in range(n)]\n",
        "bug_hint": "uses max; raises IndexError when lengths differ",
    },
    {
        "id": "qa_eval_37_sum_digits",
        "spec": "sum_digits(n: int) -> int returns the sum of the decimal digits of |n|. sum_digits(0) == 0; sum_digits(-123) == 6.",
        "function_name": "sum_digits",
        "module_name": "target",
        "buggy_code": "def sum_digits(n):\n    return sum(int(c) for c in str(n))\n",
        "fixed_code": "def sum_digits(n):\n    return sum(int(c) for c in str(abs(n)))\n",
        "bug_hint": "negative input: str(-123) starts with '-' and int('-') raises ValueError",
    },
    {
        "id": "qa_eval_38_is_palindrome",
        "spec": "is_palindrome(s: str) -> bool returns True iff s reads the same forwards and backwards, comparing case-insensitively and ignoring non-alphanumeric characters. is_palindrome('A man, a plan, a canal: Panama') == True.",
        "function_name": "is_palindrome",
        "module_name": "target",
        "buggy_code": "def is_palindrome(s):\n    return s == s[::-1]\n",
        "fixed_code": "def is_palindrome(s):\n    cleaned = ''.join(c.lower() for c in s if c.isalnum())\n    return cleaned == cleaned[::-1]\n",
        "bug_hint": "case-sensitive and includes punctuation/whitespace",
    },
    {
        "id": "qa_eval_39_merge_sorted",
        "spec": "merge_sorted(a: list[int], b: list[int]) -> list[int] takes two ascending-sorted lists and returns a single ascending-sorted list of all elements (duplicates preserved).",
        "function_name": "merge_sorted",
        "module_name": "target",
        "buggy_code": "def merge_sorted(a, b):\n    i = j = 0\n    out = []\n    while i < len(a) and j < len(b):\n        if a[i] < b[j]:\n            out.append(a[i])\n            i += 1\n        else:\n            out.append(b[j])\n            j += 1\n    return out\n",
        "fixed_code": "def merge_sorted(a, b):\n    i = j = 0\n    out = []\n    while i < len(a) and j < len(b):\n        if a[i] <= b[j]:\n            out.append(a[i])\n            i += 1\n        else:\n            out.append(b[j])\n            j += 1\n    out.extend(a[i:])\n    out.extend(b[j:])\n    return out\n",
        "bug_hint": "drops the tail of whichever list is longer (the loop ends before both are exhausted)",
    },
    {
        "id": "qa_eval_40_remove_at",
        "spec": "remove_at(lst: list, idx: int) -> list returns a NEW list with element at idx removed. Negative idx counts from the end (Python-style). Out-of-range idx returns lst unchanged.",
        "function_name": "remove_at",
        "module_name": "target",
        "buggy_code": "def remove_at(lst, idx):\n    out = lst.copy()\n    del out[idx]\n    return out\n",
        "fixed_code": "def remove_at(lst, idx):\n    n = len(lst)\n    if idx < -n or idx >= n:\n        return list(lst)\n    out = list(lst)\n    del out[idx]\n    return out\n",
        "bug_hint": "out-of-range idx raises IndexError instead of returning the input unchanged",
    },
    {
        "id": "qa_eval_41_compress_runs",
        "spec": "compress(s: str) -> str collapses consecutive identical characters into a single character. 'aaabbc' -> 'abc'. Empty string returns ''.",
        "function_name": "compress",
        "module_name": "target",
        "buggy_code": "def compress(s):\n    out = []\n    for i, c in enumerate(s):\n        if i == 0 or c != s[i + 1]:\n            out.append(c)\n    return ''.join(out)\n",
        "fixed_code": "def compress(s):\n    if not s:\n        return ''\n    out = [s[0]]\n    for c in s[1:]:\n        if c != out[-1]:\n            out.append(c)\n    return ''.join(out)\n",
        "bug_hint": "compares to s[i + 1] (next) instead of s[i - 1] (previous); also crashes on the last character (IndexError)",
    },
    {
        "id": "qa_eval_42_unique_chars",
        "spec": "all_unique(s: str) -> bool returns True iff every character in s appears exactly once (case-sensitive).",
        "function_name": "all_unique",
        "module_name": "target",
        "buggy_code": "def all_unique(s):\n    return len(s) == len(set(s.lower()))\n",
        "fixed_code": "def all_unique(s):\n    return len(s) == len(set(s))\n",
        "bug_hint": "lowercases before comparing; 'Aa' returns False even though A and a are distinct characters under the case-sensitive spec",
    },
    {
        "id": "qa_eval_43_clip_string",
        "spec": "clip(s: str, n: int) -> str returns s truncated to at most n characters. If truncation occurs, append '...' (the full result is still capped at n characters total).",
        "function_name": "clip",
        "module_name": "target",
        "buggy_code": "def clip(s, n):\n    if len(s) <= n:\n        return s\n    return s[:n] + '...'\n",
        "fixed_code": "def clip(s, n):\n    if len(s) <= n:\n        return s\n    if n <= 3:\n        return '.' * n\n    return s[:n - 3] + '...'\n",
        "bug_hint": "appends '...' AFTER taking n chars, so truncated result has length n+3 instead of n",
    },
    {
        "id": "qa_eval_44_round_half_up",
        "spec": "round_half_up(x: float) -> int rounds x to the nearest integer with half-values rounding away from zero. round_half_up(0.5)==1, round_half_up(-0.5)==-1, round_half_up(2.5)==3.",
        "function_name": "round_half_up",
        "module_name": "target",
        "buggy_code": "def round_half_up(x):\n    return round(x)\n",
        "fixed_code": "import math\n\ndef round_half_up(x):\n    if x >= 0:\n        return math.floor(x + 0.5)\n    return -math.floor(-x + 0.5)\n",
        "bug_hint": "Python's round() uses banker's rounding: round(0.5)==0, round(2.5)==2",
    },
    {
        "id": "qa_eval_45_tally_votes",
        "spec": "winner(votes: list[str]) -> str returns the string that appears most often in votes. Ties are broken by the alphabetically smallest. Empty list returns ''.",
        "function_name": "winner",
        "module_name": "target",
        "buggy_code": "def winner(votes):\n    counts = {}\n    for v in votes:\n        counts[v] = counts.get(v, 0) + 1\n    return max(counts, key=counts.get)\n",
        "fixed_code": "def winner(votes):\n    if not votes:\n        return ''\n    counts = {}\n    for v in votes:\n        counts[v] = counts.get(v, 0) + 1\n    best = max(counts.values())\n    return min(k for k, c in counts.items() if c == best)\n",
        "bug_hint": "doesn't handle ties (returns whichever was inserted first via dict iteration order) and crashes on empty input",
    },
    {
        "id": "qa_eval_46_min_distance_pair",
        "spec": "closest_pair(nums: list[float]) -> tuple[float, float] returns the two elements of nums with the smallest absolute difference, as a tuple (a, b) with a<=b. len(nums) >= 2.",
        "function_name": "closest_pair",
        "module_name": "target",
        "buggy_code": "def closest_pair(nums):\n    best = (nums[0], nums[1])\n    best_d = abs(nums[0] - nums[1])\n    for i in range(len(nums)):\n        for j in range(i, len(nums)):\n            d = abs(nums[i] - nums[j])\n            if d < best_d:\n                best = (nums[i], nums[j])\n                best_d = d\n    return best\n",
        "fixed_code": "def closest_pair(nums):\n    best = None\n    best_d = float('inf')\n    for i in range(len(nums)):\n        for j in range(i + 1, len(nums)):\n            d = abs(nums[i] - nums[j])\n            if d < best_d:\n                a, b = nums[i], nums[j]\n                best = (a, b) if a <= b else (b, a)\n                best_d = d\n    return best\n",
        "bug_hint": "inner loop starts at i (allows i==j), so distance 0 always 'wins' against the same element — also doesn't sort the returned pair",
    },
    {
        "id": "qa_eval_47_reverse_words",
        "spec": "reverse_words(s: str) -> str returns s with the order of whitespace-separated words reversed; multiple spaces collapse to single. 'the  quick  fox' -> 'fox quick the'.",
        "function_name": "reverse_words",
        "module_name": "target",
        "buggy_code": "def reverse_words(s):\n    return ' '.join(reversed(s.split(' ')))\n",
        "fixed_code": "def reverse_words(s):\n    return ' '.join(reversed(s.split()))\n",
        "bug_hint": "split(' ') retains empty tokens from runs of spaces; result has odd internal spacing",
    },
    {
        "id": "qa_eval_48_safe_get",
        "spec": "safe_get(d: dict, *keys, default=None) -> any walks a chain of keys into nested dicts. Returns default on the first missing key or non-dict intermediate.",
        "function_name": "safe_get",
        "module_name": "target",
        "buggy_code": "def safe_get(d, *keys, default=None):\n    for k in keys:\n        d = d[k]\n    return d\n",
        "fixed_code": "def safe_get(d, *keys, default=None):\n    cur = d\n    for k in keys:\n        if not isinstance(cur, dict) or k not in cur:\n            return default\n        cur = cur[k]\n    return cur\n",
        "bug_hint": "raises KeyError or TypeError on missing/non-dict keys instead of returning default",
    },
    {
        "id": "qa_eval_49_normalize_grade",
        "spec": "to_grade(score: int) -> str returns 'A' for 90<=score<=100, 'B' for 80-89, 'C' for 70-79, 'D' for 60-69, 'F' for 0-59. Out-of-range raises ValueError.",
        "function_name": "to_grade",
        "module_name": "target",
        "buggy_code": "def to_grade(score):\n    if score >= 90:\n        return 'A'\n    if score >= 80:\n        return 'B'\n    if score >= 70:\n        return 'C'\n    if score >= 60:\n        return 'D'\n    return 'F'\n",
        "fixed_code": "def to_grade(score):\n    if not 0 <= score <= 100:\n        raise ValueError('out of range')\n    if score >= 90:\n        return 'A'\n    if score >= 80:\n        return 'B'\n    if score >= 70:\n        return 'C'\n    if score >= 60:\n        return 'D'\n    return 'F'\n",
        "bug_hint": "no range check; -50 returns 'F' and 150 returns 'A'",
    },
    {
        "id": "qa_eval_50_matrix_transpose",
        "spec": "transpose(mat: list[list]) -> list[list] returns the transpose of a non-empty rectangular matrix. transpose([[1,2,3],[4,5,6]]) == [[1,4],[2,5],[3,6]].",
        "function_name": "transpose",
        "module_name": "target",
        "buggy_code": "def transpose(mat):\n    rows = len(mat)\n    cols = len(mat[0])\n    return [[mat[r][c] for r in range(cols)] for c in range(rows)]\n",
        "fixed_code": "def transpose(mat):\n    return [list(row) for row in zip(*mat)]\n",
        "bug_hint": "swapped the iteration variables in the comprehension; produces wrong shape when rows != cols",
    },
]


# --------------------------------------------------------------------- Research new tasks


# 35 new research synthesis tasks. Each follows eval_set.json schema:
# {id, shape, question, answer, aliases, notes}
NEW_RESEARCH_TASKS: list[dict] = [
    # ---- AGG shape (12 new) ----
    {
        "id": "rs_eval_16_solar_planets_rings",
        "shape": "agg",
        "question": "Of the 8 planets in the Solar System (per the IAU 2006 definition), how many have a planetary ring system?",
        "answer": "4",
        "aliases": ["4", "four"],
        "notes": "Jupiter, Saturn, Uranus, and Neptune all have ring systems (Saturn's are most prominent; Jupiter's, Uranus's, and Neptune's are faint but well-documented).",
    },
    {
        "id": "rs_eval_17_great_lakes_canada",
        "shape": "agg",
        "question": "Of the 5 Great Lakes of North America, how many have shoreline in Canada?",
        "answer": "4",
        "aliases": ["4", "four"],
        "notes": "Lake Superior, Huron, Erie, and Ontario border Canada. Lake Michigan is entirely within the United States.",
    },
    {
        "id": "rs_eval_18_g7_pacific_coast",
        "shape": "agg",
        "question": "Of the 7 G7 member countries (Canada, France, Germany, Italy, Japan, the United Kingdom, and the United States), how many have a Pacific Ocean coastline?",
        "answer": "3",
        "aliases": ["3", "three"],
        "notes": "Canada, Japan, and the United States have Pacific coastlines. France, Germany, Italy, and the UK do not.",
    },
    {
        "id": "rs_eval_19_continents_population_threshold",
        "shape": "agg",
        "question": "Of the 7 continents, how many contain at least one country whose population exceeds 200 million as of 2024 estimates?",
        "answer": "4",
        "aliases": ["4", "four"],
        "notes": "Asia (China ~1.41B, India ~1.45B, Indonesia ~280M, Pakistan ~245M), Africa (Nigeria ~230M), North America (USA ~340M), South America (Brazil ~217M). Europe's largest country by population (Russia ~143M, treating Russia as Europe) is below 200M. Oceania, Antarctica are far below. Threshold of 200M chosen specifically to make the Russia-as-Europe ambiguity moot.",
    },
    {
        "id": "rs_eval_20_brics_landlocked",
        "shape": "agg",
        "question": "Of the 5 original BRICS member countries (Brazil, Russia, India, China, South Africa), how many are landlocked?",
        "answer": "0",
        "aliases": ["0", "zero", "none"],
        "notes": "All five have ocean coastlines. Brazil (Atlantic), Russia (Arctic, Pacific, Atlantic via Baltic/Black), India (Indian), China (Pacific), South Africa (Atlantic, Indian). Trick question — agent that searches naively may invent a wrong answer.",
    },
    {
        "id": "rs_eval_21_eu_founders_french_official",
        "shape": "agg",
        "question": "Of the 6 founding members of the European Economic Community (signatories of the 1957 Treaty of Rome), how many have French as an official language at the national level today?",
        "answer": "3",
        "aliases": ["3", "three"],
        "notes": "EEC founders: Belgium, France, Italy, Luxembourg, Netherlands, West Germany. French is national-official in France, Belgium, and Luxembourg. Italian/German/Dutch are official in the others; Italy has minor French regional status (Aosta Valley) but not national.",
    },
    {
        "id": "rs_eval_22_un_security_permanent_nuclear",
        "shape": "agg",
        "question": "Of the 5 permanent members of the UN Security Council, how many are recognized nuclear-weapon states under the NPT?",
        "answer": "5",
        "aliases": ["5", "five", "all"],
        "notes": "All five P5 — China, France, Russia, the United Kingdom, and the United States — are recognized nuclear-weapon states under the Nuclear Non-Proliferation Treaty. This is by design (the NPT defines NWS as states that tested before 1 Jan 1967).",
    },
    {
        "id": "rs_eval_23_seven_summits_southern",
        "shape": "agg",
        "question": "Of the 7 Summits (highest peaks of the seven continents — Bass list: Everest, Aconcagua, Denali, Kilimanjaro, Elbrus, Vinson, Kosciuszko), how many are in the Southern Hemisphere?",
        "answer": "4",
        "aliases": ["4", "four"],
        "notes": "Aconcagua (S. America, ~32°S), Kilimanjaro (Africa, ~3°S), Vinson (Antarctica, ~78°S), Kosciuszko (Australia, ~36°S). Everest, Denali, and Elbrus are in the Northern Hemisphere.",
    },
    {
        "id": "rs_eval_24_dwarf_planets_rings",
        "shape": "agg",
        "question": "Of the 5 IAU-recognized dwarf planets (Ceres, Pluto, Haumea, Makemake, Eris), how many are known to have at least one moon?",
        "answer": "4",
        "aliases": ["4", "four"],
        "notes": "Pluto (Charon + 4), Haumea (Hi'iaka, Namaka), Makemake (MK 2), Eris (Dysnomia). Ceres has no known moon. Snippet-skim may confuse 'has a ring' (Haumea has rings too) with 'has a moon'.",
    },
    {
        "id": "rs_eval_25_grand_slams_outside_europe",
        "shape": "agg",
        "question": "Of the 4 tennis Grand Slam tournaments (Australian Open, French Open, Wimbledon, US Open), how many are held outside Europe?",
        "answer": "2",
        "aliases": ["2", "two"],
        "notes": "Australian Open (Melbourne) and US Open (New York) are outside Europe. French Open (Paris) and Wimbledon (London) are in Europe.",
    },
    {
        "id": "rs_eval_26_baltic_states_eurozone",
        "shape": "agg",
        "question": "Of the 3 Baltic states (Estonia, Latvia, Lithuania), how many use the Euro as their official currency as of 2024?",
        "answer": "3",
        "aliases": ["3", "three", "all"],
        "notes": "Estonia (2011), Latvia (2014), Lithuania (2015). All three are in the eurozone.",
    },
    {
        "id": "rs_eval_27_oceans_touch_antarctica",
        "shape": "agg",
        "question": "Of the 5 oceans of Earth (Pacific, Atlantic, Indian, Arctic, Southern), how many directly border Antarctica?",
        "answer": "1",
        "aliases": ["1", "one"],
        "notes": "By the IHO 2000 definition, only the Southern Ocean borders Antarctica. Some older conventions extend Pacific/Atlantic/Indian to the coast, but under the standard 5-ocean model only the Southern Ocean does. Trick: agents using older definitions may answer 4.",
    },
    # ---- FILTER shape (12 new) ----
    {
        "id": "rs_eval_28_g20_eu_members",
        "shape": "filter",
        "question": "Of the 19 country members of the G20 (i.e., excluding the EU itself and the African Union), how many are also EU member states as of June 2024?",
        "answer": "3",
        "aliases": ["3", "three"],
        "notes": "G20 country list: Argentina, Australia, Brazil, Canada, China, France, Germany, India, Indonesia, Italy, Japan, Mexico, Russia, Saudi Arabia, South Africa, South Korea, Turkey, UK, USA. Of those, EU members: France, Germany, Italy = 3. (UK left the EU in 2020.)",
    },
    {
        "id": "rs_eval_29_panchen_lhasa_skip",
        "shape": "filter",
        "question": "Of the 6 official languages of the United Nations (Arabic, Chinese, English, French, Russian, Spanish), how many are written using a script that has dedicated Unicode blocks BUT is also still actively written right-to-left?",
        "answer": "1",
        "aliases": ["1", "one"],
        "notes": "Only Arabic is right-to-left among the UN languages. Chinese can be vertical but modern usage is left-to-right.",
    },
    {
        "id": "rs_eval_30_nato_borders_russia",
        "shape": "filter",
        "question": "Of the 32 NATO member states (as of June 2024), how many share a land border with Russia, counting borders with the Kaliningrad exclave?",
        "answer": "6",
        "aliases": ["6", "six"],
        "notes": "Norway, Finland (joined 2023), Estonia, Latvia border mainland Russia; Lithuania and Poland border the Kaliningrad exclave. Total: 6 NATO members.",
    },
    {
        "id": "rs_eval_31_oscar_best_picture_2010s_orig_screenplay",
        "shape": "filter",
        "question": "Of the 10 Best Picture winners at the Academy Awards covering films released 2010 through 2019 (83rd through 92nd ceremonies), how many won the Academy Award for Best Original Screenplay at the same ceremony?",
        "answer": "5",
        "aliases": ["5", "five"],
        "notes": "Best Picture winners 2010-19: The King's Speech (also Original Screenplay), The Artist, Argo (Adapted Screenplay), 12 Years a Slave (Adapted), Birdman (Original Screenplay), Spotlight (Original Screenplay), Moonlight (Adapted Screenplay), The Shape of Water (no screenplay win), Green Book (Original Screenplay), Parasite (Original Screenplay). Films winning Best Picture AND Original Screenplay: The King's Speech, Birdman, Spotlight, Green Book, Parasite = 5. (Replaced ambiguous 'based on a novel/memoir' phrasing with a precise crosstab.)",
    },
    {
        "id": "rs_eval_32_olympic_summer_2000s_asia",
        "shape": "filter",
        "question": "Of the 7 Summer Olympic Games held from 2000 through 2024 inclusive (Sydney 2000, Athens 2004, Beijing 2008, London 2012, Rio 2016, Tokyo 2020, Paris 2024), how many took place in Asia?",
        "answer": "2",
        "aliases": ["2", "two"],
        "notes": "Beijing 2008 and Tokyo 2020 (held 2021) are in Asia. Sydney is Oceania, Athens/London/Paris are Europe, Rio is South America.",
    },
    {
        "id": "rs_eval_33_g7_2024_woman_head_state",
        "shape": "filter",
        "question": "Of the 7 G7 member countries, how many had a woman as their head of government as of June 2024?",
        "answer": "1",
        "aliases": ["1", "one"],
        "notes": "Italy: Giorgia Meloni (PM since October 2022). The other six (Canada/Trudeau, France/Attal, Germany/Scholz, Japan/Kishida, UK/Sunak, USA/Biden — POTUS is head of state AND government) all had male heads of government in June 2024.",
    },
    {
        "id": "rs_eval_34_currencies_top10_pop_non_decimal",
        "shape": "filter",
        "question": "Of the 10 most populous countries (per 2024 UN estimates), how many have an official currency that is NOT subdivided into 100 minor units?",
        "answer": "0",
        "aliases": ["0", "zero", "none"],
        "notes": "Top 10: India (rupee/100 paise), China (yuan/100 fen), USA (dollar/100 cents), Indonesia (rupiah/100 sen), Pakistan (rupee/100 paisa), Nigeria (naira/100 kobo), Brazil (real/100 centavos), Bangladesh (taka/100 poisha), Russia (rouble/100 kopecks), Mexico (peso/100 centavos). All decimal-subdivided. Trick question.",
    },
    {
        "id": "rs_eval_35_dwarf_planets_outside_kuiper",
        "shape": "filter",
        "question": "Of the 5 IAU-recognized dwarf planets, how many orbit primarily inside the Kuiper Belt or beyond?",
        "answer": "4",
        "aliases": ["4", "four"],
        "notes": "Pluto, Haumea, Makemake (Kuiper Belt). Eris (scattered disc, beyond Kuiper). Ceres is in the asteroid belt between Mars and Jupiter — NOT in the Kuiper region.",
    },
    {
        "id": "rs_eval_36_uefa_finals_decade_english_winners",
        "shape": "filter",
        "question": "Of the 10 UEFA Champions League finals played in the 2014-15 through 2023-24 seasons inclusive, how many were won by an English club?",
        "answer": "3",
        "aliases": ["3", "three"],
        "notes": "Winners by season: 2014-15 Barcelona, 2015-16 Real Madrid, 2016-17 Real Madrid, 2017-18 Real Madrid, 2018-19 Liverpool (English), 2019-20 Bayern Munich, 2020-21 Chelsea (English), 2021-22 Real Madrid, 2022-23 Manchester City (English), 2023-24 Real Madrid. English winners: 3.",
    },
    {
        "id": "rs_eval_37_top10_economies_landlocked",
        "shape": "filter",
        "question": "Of the 10 largest economies by nominal GDP (per IMF 2024 estimates: USA, China, Germany, Japan, India, UK, France, Italy, Brazil, Canada), how many are landlocked?",
        "answer": "0",
        "aliases": ["0", "zero", "none"],
        "notes": "All 10 have ocean coastlines. Trick: agent may answer 1+ if it confuses 'landlocked region' with 'landlocked country'.",
    },
    {
        "id": "rs_eval_38_un_official_languages_non_european",
        "shape": "filter",
        "question": "Of the 6 official languages of the United Nations, how many are not classified as Indo-European languages?",
        "answer": "2",
        "aliases": ["2", "two"],
        "notes": "Arabic (Afro-Asiatic) and Chinese (Sino-Tibetan) are non-Indo-European. English, French, Russian, Spanish are all Indo-European.",
    },
    {
        "id": "rs_eval_39_brics_official_english",
        "shape": "filter",
        "question": "Of the 5 original BRICS countries (Brazil, Russia, India, China, South Africa), how many have English as an official or constitutional language?",
        "answer": "2",
        "aliases": ["2", "two"],
        "notes": "India (English is associate official under the 1963 Official Languages Act) and South Africa (English is one of 12 official languages). Brazil's official language is Portuguese, Russia's is Russian, China's is Standard Chinese.",
    },
    # ---- GRANULAR shape (11 new — recent or precise enough to dodge memorized lookups) ----
    {
        "id": "rs_eval_40_g20_summit_hosts_2020s",
        "shape": "granular",
        "question": "Of the 5 G20 summits held from 2020 through 2024 inclusive (Riyadh 2020, Rome 2021, Bali 2022, New Delhi 2023, Rio 2024), how many took place in countries that were NOT G20 founding members in 1999?",
        "answer": "0",
        "aliases": ["0", "zero", "none"],
        "notes": "Saudi Arabia, Italy, Indonesia, India, Brazil — all G20 founding members.",
    },
    {
        "id": "rs_eval_41_nobel_lit_2020_2024_european",
        "shape": "granular",
        "question": "Of the 5 Nobel Prize in Literature laureates from 2020 through 2024 inclusive, how many were born in Europe?",
        "answer": "2",
        "aliases": ["2", "two"],
        "notes": "2020 Louise Glück (USA-born), 2021 Abdulrazak Gurnah (Zanzibar, Africa), 2022 Annie Ernaux (France), 2023 Jon Fosse (Norway), 2024 Han Kang (South Korea). Europe-born: Ernaux, Fosse = 2.",
    },
    {
        "id": "rs_eval_42_recent_world_cup_winners_european",
        "shape": "granular",
        "question": "Of the 5 most recent FIFA Men's World Cup winners (covering the 2006, 2010, 2014, 2018, and 2022 tournaments), how many were European national teams?",
        "answer": "4",
        "aliases": ["4", "four"],
        "notes": "2006 Italy, 2010 Spain, 2014 Germany, 2018 France, 2022 Argentina. European winners: Italy, Spain, Germany, France = 4. Argentina is South American.",
    },
    {
        "id": "rs_eval_43_recent_oscar_directors_non_us",
        "shape": "granular",
        "question": "Of the 5 winners of the Academy Award for Best Director for films of 2019 through 2023 (92nd through 96th ceremonies), how many were NOT born in the United States?",
        "answer": "4",
        "aliases": ["4", "four"],
        "notes": "Bong Joon-ho (South Korea, 92nd), Chloé Zhao (China, 93rd), Jane Campion (New Zealand, 94th), Daniel Kwan & Daniel Scheinert (USA-born, 95th), Christopher Nolan (UK, 96th). Non-US-born: Bong, Zhao, Campion, Nolan = 4.",
    },
    {
        "id": "rs_eval_44_recent_super_bowl_afc_champ",
        "shape": "granular",
        "question": "Of the 5 most recent Super Bowls (LV through LIX, played February 2021 through February 2025), how many were won by the AFC champion?",
        "answer": "2",
        "aliases": ["2", "two"],
        "notes": "LV: Tampa Bay (NFC), LVI: Rams (NFC), LVII: Kansas City (AFC), LVIII: Kansas City (AFC), LIX: Philadelphia (NFC). AFC wins: 2 (LVII, LVIII).",
    },
    {
        "id": "rs_eval_45_recent_grand_slam_men_outside_big3",
        "shape": "granular",
        "question": "Of the 12 men's tennis Grand Slam tournaments played from 2022 through 2024 inclusive, how many were won by a player other than Novak Djokovic, Rafael Nadal, or Roger Federer?",
        "answer": "6",
        "aliases": ["6", "six"],
        "notes": "2022: AO Nadal, FO Nadal, W Djokovic, USO Alcaraz; 2023: AO Djokovic, FO Djokovic, W Alcaraz, USO Djokovic; 2024: AO Sinner, FO Alcaraz, W Alcaraz, USO Sinner. Non-Big-3: Alcaraz (USO 22, W 23, FO+W 24 = 4) + Sinner (AO+USO 24 = 2) = 6.",
    },
    {
        "id": "rs_eval_46_recent_eurovision_non_european_geog",
        "shape": "granular",
        "question": "Of the 5 most recent Eurovision Song Contest winners (2019, 2021, 2022, 2023, 2024 — 2020 was cancelled), how many won representing a country that does not lie wholly within the European continent?",
        "answer": "0",
        "aliases": ["0", "zero", "none"],
        "notes": "2019 Netherlands, 2021 Italy, 2022 Ukraine, 2023 Sweden, 2024 Switzerland. All five are wholly European. Trick: agent may guess >0 thinking of Israel or Australia (frequent participants but not winners in this window).",
    },
    {
        "id": "rs_eval_47_recent_booker_winners_first_book",
        "shape": "granular",
        "question": "Of the 5 most recent Booker Prize for Fiction winners (2020 through 2024), how many won for their debut novel?",
        "answer": "1",
        "aliases": ["1", "one"],
        "notes": "2020 Douglas Stuart (Shuggie Bain, debut), 2021 Damon Galgut (The Promise, not debut), 2022 Shehan Karunatilaka (The Seven Moons of Maali Almeida, not debut), 2023 Paul Lynch (Prophet Song, not debut), 2024 Samantha Harvey (Orbital, not debut). Debut wins: just Stuart = 1.",
    },
    {
        "id": "rs_eval_48_recent_un_sg_european",
        "shape": "granular",
        "question": "Of the 9 Secretaries-General of the United Nations (Trygve Lie through António Guterres), how many were born in Europe?",
        "answer": "4",
        "aliases": ["4", "four"],
        "notes": "Trygve Lie (Norway, 1946-52), Dag Hammarskjöld (Sweden, 1953-61), U Thant (Burma, 1961-71), Kurt Waldheim (Austria, 1972-81), Javier Pérez de Cuéllar (Peru, 1982-91), Boutros Boutros-Ghali (Egypt, 1992-96), Kofi Annan (Ghana, 1997-2006), Ban Ki-moon (South Korea, 2007-16), António Guterres (Portugal, 2017-). European-born: Lie, Hammarskjöld, Waldheim, Guterres = 4.",
    },
    {
        "id": "rs_eval_49_recent_palme_dor_women_directors",
        "shape": "granular",
        "question": "Of the 9 Palme d'Or winners at the Cannes Film Festival in years 2015 through 2024 (no festival in 2020), how many were directed by a woman?",
        "answer": "2",
        "aliases": ["2", "two"],
        "notes": "2015 Dheepan (Audiard), 2016 I Daniel Blake (Loach), 2017 The Square (Östlund), 2018 Shoplifters (Kore-eda), 2019 Parasite (Bong), 2021 Titane (Ducournau, woman), 2022 Triangle of Sadness (Östlund), 2023 Anatomy of a Fall (Triet, woman), 2024 Anora (Baker). Women-directed: Ducournau and Triet = 2.",
    },
    {
        "id": "rs_eval_50_recent_imf_md_european",
        "shape": "granular",
        "question": "Of the 5 most recent Managing Directors of the International Monetary Fund (Horst Köhler 2000-04, Rodrigo Rato 2004-07, Dominique Strauss-Kahn 2007-11, Christine Lagarde 2011-19, Kristalina Georgieva 2019-), how many were born in Europe?",
        "answer": "5",
        "aliases": ["5", "five", "all"],
        "notes": "Köhler (Poland, then a German territory), Rato (Spain), Strauss-Kahn (France), Lagarde (France), Georgieva (Bulgaria). All five European-born. Reflects the European-MD convention at the IMF.",
    },
]


# --------------------------------------------------------------------- builder


def verify_qa_task(task: dict) -> tuple[bool, str]:
    """Run buggy and fixed implementations through Python; check for parse errors.

    We don't have ground-truth tests yet (the agent writes those), so the
    strongest verification we can do here is "both files parse and import
    cleanly, and the function is callable." Bug correctness is verified by
    eyeballing the bug_hint.
    """
    for which in ("buggy_code", "fixed_code"):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "target.py"
            p.write_text(task[which], encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, "-c", f"import sys; sys.path.insert(0, r'{td}'); import target; assert callable(getattr(target, {task['function_name']!r}))"],
                capture_output=True, text=True, timeout=10,
            )
            if proc.returncode != 0:
                return False, f"{which} did not import cleanly: {proc.stderr.strip()}"
    return True, "ok"


def build_qa_v2() -> None:
    v1_path = ROOT / "evals" / "qa" / "eval_set.json"
    v2_path = ROOT / "evals" / "qa" / "eval_v2_set.json"
    with v1_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    seen_ids = {t["id"] for t in data["tasks"]}
    print(f"QA v1: {len(data['tasks'])} tasks loaded")
    appended = 0
    failures: list[str] = []
    for t in NEW_QA_TASKS:
        if t["id"] in seen_ids:
            print(f"  skip {t['id']}: id already in v1")
            continue
        ok, msg = verify_qa_task(t)
        if not ok:
            failures.append(f"{t['id']}: {msg}")
            continue
        data["tasks"].append(t)
        seen_ids.add(t["id"])
        appended += 1
    data["split"] = "eval"
    data["description"] = (
        "Held-out QA tasks (v2 = v1 + 35 hand-curated additions for tighter CIs). "
        "Same shape as probe_set.json. Never shown to the stem during evolution."
    )
    if failures:
        print("QA verification failures:")
        for fail in failures:
            print(f"  - {fail}")
        raise SystemExit(1)
    with v2_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"QA v2 written to {v2_path}: {len(data['tasks'])} tasks ({appended} new)")


def build_research_v2() -> None:
    v1_path = ROOT / "evals" / "research" / "eval_set.json"
    v2_path = ROOT / "evals" / "research" / "eval_v2_set.json"
    with v1_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    seen_ids = {t["id"] for t in data["tasks"]}
    print(f"Research v1: {len(data['tasks'])} tasks loaded")
    appended = 0
    for t in NEW_RESEARCH_TASKS:
        if t["id"] in seen_ids:
            print(f"  skip {t['id']}: id already in v1")
            continue
        data["tasks"].append(t)
        seen_ids.add(t["id"])
        appended += 1
    data["split"] = "eval"
    data["description"] = (
        "Held-out research synthesis tasks (v2 = v1 + 35 hand-curated additions for "
        "tighter CIs). Same shapes as v1 (agg / filter / granular). Never appears in "
        "probe_set.json."
    )
    with v2_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"Research v2 written to {v2_path}: {len(data['tasks'])} tasks ({appended} new)")
    shape_counts: dict[str, int] = {}
    for t in data["tasks"]:
        s = t.get("shape", "?")
        shape_counts[s] = shape_counts.get(s, 0) + 1
    print(f"Research v2 shape breakdown: {shape_counts}")


def main() -> int:
    build_qa_v2()
    build_research_v2()
    return 0


if __name__ == "__main__":
    sys.exit(main())
