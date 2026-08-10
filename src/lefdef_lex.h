/*
 * Copyright 2026 Ben Bulent Basaran
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

// lefdef_lex.h — the token layer shared by the LEF and DEF readers.
//
// LEF and DEF are one lexical family: whitespace-insensitive token streams
// with `#` comments, `"`-quoted strings, and `;` as the STATEMENT terminator.
// Newlines carry no meaning at all.  The scanners this replaces read the
// files LINE by line, which is why a `COMPONENTS` entry wrapped across lines
// — legal, and common in tool-written DEF — was silently dropped.
//
// Deliberately NOT a statement iterator.  A `;`-only view fits DEF but not
// LEF, whose block structure is semicolon-free:
//
//     MACRO m            <- no ';'
//       SIZE 1 BY 2 ;    <- ';'
//       PIN a            <- no ';'
//         PORT           <- no ';'
//           RECT 0 0 1 1 ;
//         END            <- no ';'
//       END a
//     END m
//
// and `LAYER M1 ;` inside a PORT is a statement while `LAYER M1 … END M1` at
// top level is a block — the same keyword, told apart only by context that
// the PARSER has and a lexer cannot.  So this yields tokens plus a
// `statement()` convenience for the `;`-terminated case, and the parser stays
// a recursive descent that knows where it is.
//
// Errors carry a line number.  A malformed technology file has to say WHERE,
// or the reader is just a different way to produce wrong geometry silently.

#pragma once

#include <cctype>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace buda {

class LefDefLexer {
public:
    LefDefLexer(std::string text, std::string where)
        : text_(std::move(text)), where_(std::move(where)) {}

    // Next token, or false at end of input.
    bool next(std::string& tok) {
        if (!fill()) return false;
        tok = std::move(pending_);
        has_pending_ = false;
        return true;
    }

    // Next token without consuming it; empty string at end of input.
    const std::string& peek() {
        static const std::string kEnd;
        return fill() ? pending_ : kEnd;
    }

    bool eof() { return !fill(); }

    // Line of the token most recently returned by next() — i.e. the one the
    // caller is complaining about, not the one after it.
    int line() const { return tok_line_; }

    // Tokens up to and including the next `;` (the `;` itself is dropped).
    // False at end of input with nothing read.  An unterminated statement is
    // a hard error: silently accepting the tail of a truncated file is how a
    // half-read library turns into a plausible-looking design.
    bool statement(std::vector<std::string>& out) {
        out.clear();
        std::string t;
        const int start = line_;
        while (next(t)) {
            if (t == ";") return true;
            out.push_back(std::move(t));
        }
        if (out.empty()) return false;
        throw std::runtime_error(
            where_ + ":" + std::to_string(start) +
            ": unterminated statement (no ';' before end of file): " +
            join(out));
    }

    [[noreturn]] void fail(const std::string& msg) const {
        throw std::runtime_error(
            where_ + ":" + std::to_string(tok_line_) + ": " + msg);
    }

    // Consume a token, failing with context when the file ends first.
    std::string expect(const std::string& what) {
        std::string t;
        if (!next(t)) fail("expected " + what + ", got end of file");
        return t;
    }

    double expect_number(const std::string& what) {
        const std::string t = expect(what);
        try {
            size_t used = 0;
            const double v = std::stod(t, &used);
            if (used == t.size()) return v;
        } catch (const std::exception&) {
            // fall through to the shared diagnostic
        }
        fail("expected a number for " + what + ", got '" + t + "'");
    }

    const std::string& where() const { return where_; }

    static std::string join(const std::vector<std::string>& toks) {
        std::string s;
        for (const auto& t : toks) { if (!s.empty()) s += ' '; s += t; }
        return s;
    }

private:
    // Produce the next token into pending_, if there isn't one already.
    bool fill() {
        if (has_pending_) return true;
        for (;;) {
            // Whitespace and comments.
            while (pos_ < text_.size()) {
                const char c = text_[pos_];
                if (c == '\n') { ++line_; ++pos_; }
                else if (std::isspace(static_cast<unsigned char>(c))) ++pos_;
                else if (c == '#') {                     // to end of line
                    while (pos_ < text_.size() && text_[pos_] != '\n') ++pos_;
                } else break;
            }
            if (pos_ >= text_.size()) return false;

            tok_line_ = line_;
            const char c = text_[pos_];
            if (c == ';') {                              // its own token
                ++pos_;
                pending_ = ";";
            } else if (c == '"') {                       // quoted string
                ++pos_;
                std::string s;
                while (pos_ < text_.size() && text_[pos_] != '"') {
                    if (text_[pos_] == '\n') ++line_;
                    s += text_[pos_++];
                }
                if (pos_ >= text_.size())
                    fail("unterminated quoted string");
                ++pos_;                                   // closing quote
                pending_ = std::move(s);
            } else {
                const size_t start = pos_;
                while (pos_ < text_.size()) {
                    const char d = text_[pos_];
                    if (std::isspace(static_cast<unsigned char>(d)) ||
                        d == ';' || d == '#' || d == '"') break;
                    // A backslash escapes the next character (DEF's `\[`,
                    // `\]` in hierarchical names): take both, verbatim, and
                    // let the caller normalize.
                    if (d == '\\' && pos_ + 1 < text_.size()) ++pos_;
                    ++pos_;
                }
                pending_ = text_.substr(start, pos_ - start);
            }
            has_pending_ = true;
            return true;
        }
    }

    std::string text_;
    std::string where_;
    size_t pos_ = 0;
    int line_ = 1;         // line of the NEXT character to scan
    int tok_line_ = 1;     // line the pending/most-recent token started on
    std::string pending_;
    bool has_pending_ = false;
};

}  // namespace buda
