/** The prose guard, as the browser applies it to the literals of a RichText. The server writes
 * every number a person reads, so prose the agent wrote may hold no digit, no numeral in any
 * script, no number word and no placeholder: a value arrives as a `ref` segment with its own
 * provenance id. This mirrors `agents/prose_guard.py` in the room; the rule is deliberately
 * strict (no allowance for years or formulas). */

const NUMBER_WORDS = [
  "zero",
  "two",
  "three",
  "four",
  "five",
  "six",
  "seven",
  "eight",
  "nine",
  "ten",
  "eleven",
  "twelve",
  "thirteen",
  "fourteen",
  "fifteen",
  "sixteen",
  "seventeen",
  "eighteen",
  "nineteen",
  "twenty",
  "thirty",
  "forty",
  "fifty",
  "sixty",
  "seventy",
  "eighty",
  "ninety",
  "hundred",
  "hundreds",
  "thousand",
  "thousands",
  "million",
  "millions",
  "billion",
  "billions",
  "half",
  "halves",
  "halve",
  "halved",
  "halving",
  "double",
  "doubles",
  "doubled",
  "doubling",
  "triple",
  "tripled",
  "quadruple",
  "quadrupled",
  "twice",
  "thrice",
  "dozen",
  "dozens",
  "quarter",
  "quarters",
];
const WORD = new RegExp(
  "\\b(?:" + [...NUMBER_WORDS].sort((a, b) => b.length - a.length).join("|") + ")\\b",
  "i",
);
// Numeric Unicode categories, and the CJK numerals (category Lo, so the category test misses them).
const DIGIT = /[\p{Nd}\p{No}\p{Nl}]/u;
const CJK = /[〇零一二三四五六七八九十百千万億兆壱弐参拾壹贰叁肆伍陆柒捌玖貳參陸]/u;

export const MAX_LITERAL = 600;

/** Every reason the text may not be shown as agent prose; empty when it may. */
export function proseProblems(text: string): string[] {
  const problems: string[] = [];
  const normal = text.normalize("NFKC");
  if (DIGIT.test(text) || DIGIT.test(normal) || CJK.test(normal))
    problems.push("it contains a digit or numeral");
  const word = WORD.exec(normal);
  if (word) problems.push(`it contains the number word "${word[0].toLowerCase()}"`);
  if (normal.includes("{{") || normal.includes("}}")) problems.push("it contains a placeholder");
  if (text.length > MAX_LITERAL) problems.push("it is too long");
  return problems;
}

export const proseOk = (text: string) => proseProblems(text).length === 0;
