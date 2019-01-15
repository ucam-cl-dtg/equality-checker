import re
import ast
import tokenize
import unicodedata
import sympy
import sympy.abc
from sympy.parsing import sympy_parser
from sympy.core.numbers import Integer, Float, Rational
from sympy.core.basic import Basic


__all__ = ["UnsafeInputException", "ParsingException", "cleanup_string", "is_valid_symbol", "parse_expr"]


# What constitutes a relation?
RELATIONS = {ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">="}

# Unicode number and fraction name information:
NUMBERS = {"ZERO": 0, "ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5, "SIX": 6, "SEVEN": 7, "EIGHT": 8, "NINE": 9}
FRACTIONS = {"HALF": 2, "THIRD": 3, "QUARTER": 4, "FIFTH": 5, "SIXTH": 6, "SEVENTH": 7, "EIGHTH": 8, "NINTH": 9, "TENTH": 10}
FRACTIONS.update({"{}S".format(key): value for key, value in FRACTIONS.items() if key != "HALF"})

# We need to be able to sanitise user input. Whitelist allowed characters:
ALLOWED_CHARACTER_LIST = ["\x20",            # space
                          "\x28-\x29",       # left and right brackets
                          "\x2A-\x2F",       # times, plus, comma, minus, decimal point, divide
                          "\x30-\x39",       # numbers 0-9
                          "\x3C-\x3E",       # less than, equal, greater than
                          "\x41-\x5A",       # uppercase letters A-Z
                          "\x5E-\x5F",       # caret symbol, underscore
                          "\x61-\x7A",       # lowercase letters a-z
                          "\u00B1"]          # plus or minus symbol

# Join these into a regular expression that matches everything except allowed characters:
UNSAFE_CHARACTERS_REGEX = r"[^" + "".join(ALLOWED_CHARACTER_LIST) + r"]+"
# Match all non-ASCII characters:
NON_ASCII_CHAR_REGEX = r"[^\x00-\x7F]+"
# Symbols may only contain 0-9, A-Z, a-z and underscores:
NON_SYMBOL_REGEX = r"[^\x30-\x39\x41-\x5A\x61-\x7A\x5F]+"


#####
# Parsing Cleanup
#####

class ParsingException(ValueError):
    """An exception to be raised when parsing fails."""
    pass


class UnsafeInputException(ValueError):
    """An exception to be raised when unexpected input is provided."""
    pass


def process_unicode_chars(match_object):
    """Clean a string of Unicode characters into Python maths characters if possible."""
    result = ""
    prev_name = None
    for char in match_object.group(0):
        name = unicodedata.name(char, None)

        if name is None:
            result += char
        elif name.startswith("SUPERSCRIPT") and name.split()[1] in NUMBERS:
            number = name.split()[1]
            # Check if this is a continuation of a exponent, or a new one.
            if prev_name is not None and prev_name.startswith("SUPERSCRIPT"):
                result += "{0:d}".format(NUMBERS[number])
            else:
                result += "**{0:d}".format(NUMBERS[number])
        elif name.startswith("SUBSCRIPT") and name.split()[1] in NUMBERS:
            number = name.split()[1]
            # Check if this is a continuation of a subscript, or a new one.
            if prev_name is not None and prev_name.startswith("SUBSCRIPT"):
                result += "{0:d}".format(NUMBERS[number])
            else:
                result += "_{0:d}".format(NUMBERS[number])
        elif name.startswith("VULGAR FRACTION"):
            numerator_name = name.split()[2]
            denominator_name = name.split()[3]
            if numerator_name in NUMBERS and denominator_name in FRACTIONS:
                result += "({0:d}/{1:d})".format(NUMBERS[numerator_name], FRACTIONS[denominator_name])
            else:
                result += char
        elif name in ["MULTIPLICATION SIGN", "ASTERISK OPERATOR"]:
            result += "*"
        elif name in ["DIVISION SIGN", "DIVISION SLASH"]:
            result += "/"
        elif name in ["LESS-THAN OR EQUAL TO", "LESS-THAN OR SLANTED EQUAL TO"]:
            result += "<="
        elif name in ["GREATER-THAN OR EQUAL TO", "GREATER-THAN OR SLANTED EQUAL TO"]:
            result += ">="
        else:
            result += char

        prev_name = name
    return result


def cleanup_string(string, *, reject_unsafe_input):
    """Some simple sanity checking and cleanup to perform on passed in strings.

       Since arbitrary strings are passed in, and 'eval' is used implicitly by
       sympy; try and remove the worst offending things from strings.
    """
    # Flask gives us unicode objects anyway, the command line might not!
    if not isinstance(string, str):
        string = str(string.decode('utf-8'))  # We'll hope it's UTF-8
    # Swap any known safe Unicode characters with their ASCII equivalents:
    string = re.sub(NON_ASCII_CHAR_REGEX, process_unicode_chars, string)
    # Replace all non-whitelisted characters in the input:
    string = re.sub(UNSAFE_CHARACTERS_REGEX, '?', string)
    if reject_unsafe_input:
        # If we have non-whitelisted characters, raise an exception:
        if "?" in string:
            # We replaced all non-whitelisted characters with '?' (and '?' is not whitelisted)
            # so if any '?' characters exist the string must have contained bad input.
            raise UnsafeInputException("Unexpected input characters provided!")
    else:
        # otherwise just swap the blacklisted characters for spaces and proceed.
        string = string.replace("?", " ")
    # Further cleanup, because some allowed characters are only allowed in certain circumstances:
    string = re.sub(r'([^0-9])\.([^0-9])', '\g<1> \g<2>', string)  # Don't allow the . character between non-numbers
    string = re.sub(r'(.?)\.([^0-9])', '\g<1> \g<2>', string)  # Don't allow the . character before a non-numeric character,
    #                                                            but have to allow it after for cases like (.5) which are valid.
    string = string.replace("lambda", "lamda").replace("Lambda", "Lamda")  # We can't override the built-in keyword
    string = string.replace("__", " ")  # We don't need double underscores, exploits do
    string = re.sub(r'(?<![=<>])=(?![=<>])', '==', string)  # Replace all single equals signs with double equals
    return string


def is_valid_symbol(string):
    """Test whether a string can be a valid symbol.

       Useful for filtering out functions and operators, and for blacklisting
       metasymbols starting with an underscore.
    """
    if len(string) == 0:
        return False
    if re.search(NON_SYMBOL_REGEX, string) is not None:
        return False
    if string.startswith("_"):
        return False
    return True


#####
# Custom Symbol / Function / Operator Classes:
#####

class Equal(sympy.Equality):
    """A custom class to override sympy.Equality's str method."""
    def __str__(self):
        """Print the equation in a nice way!"""
        return "{0} == {1}".format(self.lhs, self.rhs)

    def __repr__(self):
        """Print the equation in a nice way!"""
        return str(self)


def logarithm(argument, base=10, **kwargs):
    """Enforce that the default base of logarithms is the more intuitive base 10.

       SymPy does what many maths packages do, and defaults to the natural
       logarithm for 'log'.
    """
    return sympy.log(argument, base, **kwargs)


def factorial(n):
    """Stop sympy blindly calculating factorials no matter how large.

       If 'n' is a number of some description, ensure that it is smaller than
       a cutoff, otherwise sympy will simply evaluate it, no matter how long that
       may take to complete!
       - 'n' should be a sympy object, that sympy.factorial(...) can use.
    """
    if isinstance(n, (Integer, Float, Rational)) and n > 50:
        raise ValueError("[Factorial]: Too large integer to compute factorial effectively!")
    else:
        return sympy.factorial(n)


#####
# Custom SymPy Parser Transformations:
#####

def _auto_symbol(tokens, local_dict, global_dict):
    """Replace the sympy builtin auto_symbol with a much more aggressive version.

       We have to replace this, because SymPy attempts to be too accepting of
       what it considers to be valid input and allows Pythonic behaviour.
       We only really want pure mathematics notations where possible!
    """
    result = []
    # As with all tranformations, we have to iterate through the tokens and
    # return the modified list of tokens:
    for tok in tokens:
        tokNum, tokVal = tok
        if tokNum == tokenize.NAME:
            name = tokVal
            # Check if the token name is in the local/global dictionaries.
            # If it is, convert it correctly, otherwise leave untouched.
            if name in local_dict:
                result.append((tokenize.NAME, name))
                continue
            elif name in global_dict:
                obj = global_dict[name]
                if isinstance(obj, (Basic, type)) or callable(obj):
                    # If it's a function/basic class, don't convert it to a Symbol!
                    result.append((tokenize.NAME, name))
                    continue
            result.extend([
                (tokenize.NAME, 'Symbol'),
                (tokenize.OP, '('),
                (tokenize.NAME, repr(str(name))),
                (tokenize.OP, ')'),
            ])
        else:
            result.append((tokNum, tokVal))

    return result


def _split_symbols_implicit_precedence(tokens, local_dict, global_dict):
    """Replace the sympy builtin split_symbols with a version respecting implicit multiplcation.

       By replacing this we can better cope with expressions like 1/xyz being
       equivalent to 1/(x*y*z) rather than (y*z)/x as is the default. However it
       cannot address issues like 1/2x becoming (1/2)*x rather than 1/(2*x), because
       Python's tokeniser does not respect whitespace and so cannot distinguish
       between '1/2 x' and '1/2x'.

       This transformation is unlikely to be used, but is provided as proof of concept.
    """
    result = []
    split = False
    split_previous = False
    for tok in tokens:
        if split_previous:
            # throw out closing parenthesis of Symbol that was split
            split_previous = False
            continue
        split_previous = False
        if tok[0] == tokenize.NAME and tok[1] == 'Symbol':
            split = True
        elif split and tok[0] == tokenize.NAME:
            symbol = tok[1][1:-1]
            if sympy_parser._token_splittable(symbol):
                # If we're splitting this symbol, wrap it in brackets by adding
                # them before the call to Symbol:
                result = result[:-2] + [(tokenize.OP, '(')] + result[-2:]
                for char in symbol:
                    if char in local_dict or char in global_dict:
                        # Get rid of the call to Symbol
                        del result[-2:]
                        result.extend([(tokenize.NAME, "{}".format(char)),
                                       (tokenize.NAME, 'Symbol'), (tokenize.OP, '(')])
                    else:
                        result.extend([(tokenize.NAME, "'{}'".format(char)), (tokenize.OP, ')'),
                                       (tokenize.NAME, 'Symbol'), (tokenize.OP, '(')])
                # Delete the last two tokens: get rid of the extraneous
                # Symbol( we just added
                # Also, set split_previous=True so will skip
                # the closing parenthesis of the original Symbol
                del result[-2:]
                split = False
                split_previous = True
                # Then close the extra brackets we added:
                result.append((tokenize.OP, ')'))
                continue
            else:
                split = False
        result.append(tok)
    return result


#####
# Customised SymPy Internals:
#####

def _evaluateFalse(s):
    """Replaces operators with the SymPy equivalents and set evaluate=False.

       Unlike the built-in evaluateFalse(...), we want to use a slightly more
       sophisticated EvaluateFalseTransformer and make operators AND functions
       evaluate=False.
        - 's' should be a string of Python code for the maths abstract syntax tree.
    """
    node = ast.parse(s)
    node = _EvaluateFalseTransformer().visit(node)
    # node is a Module, we want an Expression
    node = ast.Expression(node.body[0].value)

    return ast.fix_missing_locations(node)


class _EvaluateFalseTransformer(sympy_parser.EvaluateFalseTransformer):
    """Extend default SymPy EvaluateFalseTransformer to affect functions too.

       The SymPy version does not force function calls to be 'evaluate=False',
       which means expressions like "log(x, 10)" get simplified to "log(x)/log(10)"
       or "cos(-x)" becomes "cos(x)". For our purposes, this is unhelpful and so
       we also prevent this from occuring.

       Currently there is a list of functions not to transform, because some do
       not support the "evaluate=False" argument. This isn't particularly nice or
       future proof!
    """

    evaluate_false_keyword = ast.keyword(arg='evaluate', value=ast.Name(id='False', ctx=ast.Load()))

    def visit_Call(self, node):
        """Ensure all function calls are 'evaluate=False'."""
        # Since we have overridden the visit method, we are now responsible for
        # ensuring all child nodes are visited too. This is done most simply by
        # calling generic_visit(...) on ourself:
        self.generic_visit(node)
        # FIXME: Some functions cannot accept "evaluate=False" as an argument
        # without their __new__() method raising a TypeError. There is probably
        # some underlying reason which we could take into account of.
        # For now, blacklist those known to be problematic:
        _ignore_functions = ["Integer", "Float", "Symbol", "factorial", "sqrt", "Sqrt"]
        if node.func.id in _ignore_functions:
            # print "\tIgnoring function: {}".format(node.func.id)
            pass
        else:
            # print "\tModifying function: {}".format(node.func.id)
            node.keywords.append(self.evaluate_false_keyword)
        # We must return the node, modified or not:
        return node

    def visit_Compare(self, node):
        """Ensure all comparisons use sympy classes with 'evaluate=False'."""
        # Can't cope with comparing multiple inequalities:
        if len(node.comparators) > 1:
            raise TypeError("Cannot parse nested inequalities!")
        # As above, must ensure child nodes are visited:
        self.generic_visit(node)
        # Use the custom Equals class if equality, otherwise swap with a know relation:
        operator_class = node.ops[0].__class__
        if isinstance(node.ops[0], ast.Eq):
            return ast.Call(func=ast.Name(id='Eq', ctx=ast.Load()), args=[node.left, node.comparators[0]], keywords=[self.evaluate_false_keyword])
        elif operator_class in RELATIONS:
            return ast.Call(func=ast.Name(id='Rel', ctx=ast.Load()), args=[node.left, node.comparators[0], ast.Str(RELATIONS[operator_class])], keywords=[self.evaluate_false_keyword])
        else:
            # An unknown type of relation. Leave alone:
            return node

#    def visit(self, node):
#        """Visit every node in the tree."""
#        print ast.dump(node)
#        self.generic_visit(node)
#        return node

#####
# Custom Parsers:
#####

# These constants are needed to address some security issues.
# We don't want to use the default transformations, and we need to use a
# whitelist of functions the parser should allow to match.
_TRANSFORMS = (
    sympy_parser.auto_number, _auto_symbol,
    sympy_parser.convert_xor, sympy_parser.split_symbols,
    sympy_parser.implicit_multiplication, sympy_parser.function_exponentiation
)

_GLOBAL_DICT = {
    "Symbol": sympy.Symbol, "Integer": sympy.Integer,
    "Float": sympy.Float, "Rational": sympy.Rational,
    "Mul": sympy.Mul, "Pow": sympy.Pow, "Add": sympy.Add,
    "Rel": sympy.Rel, "Eq": Equal,
    "Derivative": sympy.Derivative, "diff": sympy.Derivative,
    "sin": sympy.sin, "cos": sympy.cos, "tan": sympy.tan,
    "Sin": sympy.sin, "Cos": sympy.cos, "Tan": sympy.tan,
    "arcsin": sympy.asin, "arccos": sympy.acos, "arctan": sympy.atan,
    "asin": sympy.asin, "acos": sympy.acos, "atan": sympy.atan,
    "ArcSin": sympy.asin, "ArcCos": sympy.acos, "ArcTan": sympy.atan,
    "sinh": sympy.sinh, "cosh": sympy.cosh, "tanh": sympy.tanh,
    "arcsinh": sympy.asinh, "arccosh": sympy.acosh, "arctanh": sympy.atanh,
    "asinh": sympy.asinh, "acosh": sympy.acosh, "atanh": sympy.atanh,
    "cosec": sympy.csc, "sec": sympy.sec, "cot": sympy.cot,
    "Csc": sympy.csc, "Sec": sympy.sec, "Cot": sympy.cot,
    "arccosec": sympy.acsc, "arcsec": sympy.asec, "arccot": sympy.acot,
    "acsc": sympy.acsc, "asec": sympy.asec, "acot": sympy.acot,
    "ArcCsc": sympy.acsc, "ArcSec": sympy.asec, "ArcCot": sympy.acot,
    "cosech": sympy.csch, "sech": sympy.sech, "coth": sympy.coth,
    "exp": sympy.exp, "log": logarithm, "ln": sympy.ln,
    "Exp": sympy.exp, "Log": logarithm, "Ln": sympy.ln,
    # "factorial": factorial,  "Factorial": factorial,
    "sqrt": sympy.sqrt, "abs": sympy.Abs,
    "Sqrt": sympy.sqrt, "Abs": sympy.Abs
}

_PARSE_HINTS = {
    "constant_pi": {"pi": sympy.pi},
    "constant_e": {"e": sympy.E},
    "imaginary_i": {"i": sympy.I},
    "imaginary_j": {"j": sympy.I},
    "natural_logarithm": {"log": sympy.log, "Log": sympy.log}
}


def parse_expr(expression_str, *, local_dict=None, hints=None):
    """A copy of sympy.sympy_parser.parse_expr(...) which prevents all evaluation.

       Arbitrary untrusted input should be cleaned using "cleanup_string" before
       calling this method.
       This is almost a direct copy of the SymPy code, but it also converts inline
       relations like "==" or ">=" to the Relation class to prevent evaluation
       and uses a more aggresive set of transformations and better prevents any
       evaluation. It also ignores the 'global_dict', 'transformations' and
       'evaluate' arguments of the original function.
       Hints can be provided to choose between ambiguous parsings, like 'i' being
       either a letter or sqrt(-1). These should be values from _PARSE_HINTS.
    """
    if not isinstance(expression_str, str):
        return None
    elif expression_str == "" or len(expression_str) == 0:
        return None

    # Ensure the local dictionary is valid:
    if local_dict is None or not isinstance(local_dict, dict):
        local_dict = {}

    # If there are parse hints, add them to the local dictionary:
    if hints is not None and isinstance(hints, (list, tuple)):
        for hint in hints:
            if hint in _PARSE_HINTS:
                local_dict.update(_PARSE_HINTS[hint])

    # FIXME: Avoid parsing issues with notation for Python longs.
    # E.g. the string '2L' should not be interpreted as "two stored as a long".
    # For now, just add a space to force desired behaviour:
    expression_str = re.sub(r'([0-9])([lL])', '\g<1> \g<2>', expression_str)

    try:
        code = sympy_parser.stringify_expr(expression_str, local_dict, _GLOBAL_DICT, _TRANSFORMS)
        ef_code = _evaluateFalse(code)
        code_compiled = compile(ef_code, '<string>', 'eval')
        return sympy_parser.eval_expr(code_compiled, local_dict, _GLOBAL_DICT)
    except (tokenize.TokenError, SyntaxError, TypeError, AttributeError, sympy.SympifyError) as e:
        print(("ERROR: {0} - {1}".format(type(e).__name__, str(e))).strip(":- "))
        raise ParsingException