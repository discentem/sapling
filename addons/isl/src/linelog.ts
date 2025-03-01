/**
 * Portions Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */

/*

Copyright (c) 2020 Jun Wu

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

*/

import type {RecordOf, ValueObject} from 'immutable';
import type {LRUWithStats} from 'shared/LRU';

import {assert} from './utils';
// Read D43857949 about the choice of the diff library.
import diffSequences from 'diff-sequences';
import {hash, List, Record} from 'immutable';
import {cached, LRU} from 'shared/LRU';
import {SelfUpdate} from 'shared/immutableExt';
import {unwrap} from 'shared/utils';

/** Operation code. */
enum Op {
  /** Unconditional jump. */
  J = 0,
  /** Jump if the current rev >= operand. */
  JGE = 1,
  /** Jump if the current rev < operand. */
  JL = 2,
  /** Append a line. */
  LINE = 3,
  /** End execution. */
  END = 4,
}

/** J instruction. */
const J = Record(
  {
    /** Opcode: J */
    op: Op.J,
    /** Program counter (offset to jump). */
    pc: 0,
  },
  'J',
);

/** JGE instruction. */
const JGE = Record(
  {
    /** Opcode: JGE */
    op: Op.JGE,
    /** `rev` to test. */
    rev: 0,
    /** Program counter (offset to jump). */
    pc: 0,
  },
  'JGE',
);

/** JL instruction. */
const JL = Record(
  {
    /** Opcode: JL */
    op: Op.JL,
    /** `rev` to test. */
    rev: 0,
    /** Program counter (offset to jump). */
    pc: 0,
  },
  'JL',
);

/** LINE instruction. */
const LINE = Record(
  {
    /** Opcode: LINE */
    op: Op.LINE,
    /** `rev` to test. */
    rev: 0,
    /** Line content. Includes EOL. */
    data: '',
  },
  'LINE',
);

/** END instruction. */
const END = Record(
  {
    /** Opcode: END */
    op: Op.END,
  },
  'END',
);

/** Program counter (offset to instructions). */
type Pc = number;

/** Revision number. Usually starts from 1. Larger number means newer versions. */
type Rev = number;

/** Index of a line. Starts from 0. */
type LineIdx = number;

/** Instruction. */
type Inst = Readonly<
  | {op: Op.J; pc: Pc}
  | {op: Op.END}
  | ((
      | {op: Op.JGE; rev: Rev; pc: Pc}
      | {op: Op.JL; rev: Rev; pc: Pc}
      | {op: Op.LINE; rev: Rev; data: string}
    ) &
      Record<{rev: Rev}>)
>;

/** Information about a line. Internal (`lines`) result of `LineLog.checkOut`. */
interface LineInfo {
  /** Line content. Includes EOL. */
  data: string;
  /** Added by the given rev. */
  rev: Rev;
  /** Produced by the instruction at the given offset. */
  pc: Pc;
  /**
   * Whether the line is deleted.
   * This is always `false` if `checkOut(rev, None)`.
   * It might be `true` when checking out a range of revisions
   * (aka. `start` passed to `checkOut` is not `null`).
   */
  deleted: boolean;
}

/** A "flatten" line. Result of `LineLog.flatten()`. */
interface FlattenLine {
  /** The line is present in the given revisions. */
  revs: Readonly<Set<Rev>>;
  /** Content of the line, including `\n`. */
  data: string;
}

/**
 * List of instructions.
 *
 * This is a wrapper of `List<Inst>` for more efficient `hashCode` and `equals`
 * calculations. The default `hashCode` from `immutable.js` scans the whole
 * `List`. In this implementation we keep 2 internal values: hash and str. The
 * `hash` is used for hashCode, and the `str` is an append-only string that
 * tracks the `editChunk` and other operations to `List<Inst>` for testing
 * equality.
 *
 * You might have noticed that the `str` equality might not match the
 * `List<Inst>` equality. For example, if we remap 1 to 2, then remap 2 to 1,
 * the `List<Inst>` is not changed, but the `str` is changed. It is okay to
 * treat the linelogs as different in this case as we almost always immediately
 * rebuild linelogs after a `remap`. It's important to make sure `recordText`
 * with the same text list gets cache hit.
 */
class Code implements ValueObject {
  constructor(
    private instList: List<Inst> = List([END() as Inst]),
    private __hash: Readonly<number> = 0,
    private __valueOf: Readonly<string> = '',
  ) {}

  getSize(): number {
    return this.instList.size;
  }

  get(pc: Pc): Readonly<Inst> | undefined {
    return this.instList.get(pc);
  }

  valueOf(): string {
    return this.__valueOf;
  }

  equals(other: Code): boolean {
    return this.__valueOf === other.__valueOf;
  }

  hashCode(): number {
    return this.__hash;
  }

  editChunk(
    aRev: Rev,
    a1: LineIdx,
    a2: LineIdx,
    bRev: Rev,
    bLines: string[],
    [aLines, aLinesMutable]: [LineInfo[], true] | [Readonly<LineInfo[]>, false],
  ): Code {
    const start = this.instList.size;

    assert(a1 <= a2, 'illegal chunk (a1 < a2)');
    assert(a2 <= aLines.length, 'out of bound a2 (wrong aRev?)');

    // See also https://sapling-scm.com/docs/internals/linelog/#editing-linelog
    // # Before             # After
    // # (pc): Instruction  # (pc): Instruction
    //       : ...                : ...
    //     a1: <a1 Inst>      a1Pc: J start
    //   a1+1: ...          a1Pc+1: ...
    //       : ...                : ...
    //     a2: ...            a2Pc: ...
    //       : ...                : ...
    //    len: N/A           start: JL brev b2Pc      [1]
    //                            : LINE brev b1      [1]
    //                            : LINE brev b1+1    [1]
    //                            : ...               [1]
    //                            : LINE brev b2-1    [1]
    //                        b2Pc: JGE brev a2Pc     [2]
    //                            : <a1 Inst> (moved) [3]
    //                            : J a1Pc+1          [4]
    // [1]: Only present if `bLines` is not empty.
    // [2]: Only present if `a1 < a2`.
    //      There are 2 choices for "a2Pc":
    //      - The a2 line exactly: aLines[a2].pc
    //      - The next instruction of the "a2 -1" line: aLines[a2 - 1].pc + 1
    //      We pick the latter to avoid overly aggressive deletion.
    //      The original C implementation might pick the former when editing
    //      the last rev for performance optimization.
    // [3]: <a1 Inst> could be LINE or END.
    // [4]: As an optimization, this is only present if <a1 Inst> is not END.
    //
    // As an optimization to make reorder less restrictive, we treat insertion
    // (a1 == a2) at the beginning of another insertion (<a1 Inst> is after a
    // <JL>) specially by patching the <JL> instruction instead of <a1 Inst>
    // and make sure the new <JL> (for this edit) is before the old <JL>.
    // See the [*] lines below for differences with the above:
    //
    // # Before             # After
    // # (pc): Instruction  # (pc): Instruction
    //       : ...                : ...
    //       : <JL>         a1Pc-1: J start           [*]
    //     a1: <a1 Inst>      a1Pc: ... (unchanged)   [*]
    //       : ...                : ...
    //    len: N/A           start: JL brev b2Pc
    //                            : ...
    //                        b2Pc: <JL> (moved)      [*]
    //                            : J a1Pc            [*]
    const newInstList = this.instList.withMutations(origCode => {
      let code = origCode;
      const a1Pc = aLines[a1].pc;
      let jlInst = a1Pc > 0 && a1 === a2 ? code.get(a1Pc - 1) : undefined;
      if (jlInst?.op !== Op.JL) {
        jlInst = undefined;
      }
      if (bLines.length > 0) {
        const b2Pc = start + bLines.length + 1;
        code.push(JL({rev: bRev, pc: b2Pc}) as Inst);
        bLines.forEach(line => {
          code = code.push(LINE({rev: bRev, data: line}) as Inst);
        });
        assert(b2Pc === code.size, 'bug: wrong pc');
      }
      if (a1 < a2) {
        assert(jlInst === undefined, 'no deletions when jlInst is set');
        const a2Pc = aLines[a2 - 1].pc + 1;
        code = code.push(JGE({rev: bRev, pc: a2Pc}) as Inst);
      }
      if (aLinesMutable && jlInst === undefined) {
        aLines[a1] = {...aLines[a1], pc: code.size};
      }
      if (jlInst === undefined) {
        const a1Inst = unwrap(code.get(a1Pc));
        code = code.push(a1Inst);
        if (a1Inst.op /* LINE or END */ !== Op.END) {
          code = code.push(J({pc: a1Pc + 1}) as Inst);
        }
        code = code.set(a1Pc, J({pc: start}) as Inst);
      } else {
        code = code
          .push(jlInst)
          .push(J({pc: a1Pc}) as Inst)
          .set(a1Pc - 1, J({pc: start}) as Inst);
      }
      return code;
    });

    if (aLinesMutable) {
      const newLines = bLines.map((s, i) => {
        return {data: s, rev: bRev, pc: start + 1 + i, deleted: false};
      });
      aLines.splice(a1, a2 - a1, ...newLines);
    }

    const newValueOf = `E${aRev},${a1},${a2},${bRev},${bLines.join('')}`;
    return this.newCode(newInstList, newValueOf);
  }

  remapRevs(revMap: Map<Rev, Rev>): [Code, Rev] {
    let newMaxRev = 0;
    const newInstList = this.instList
      .map(c => {
        if (c.op === Op.JGE || c.op === Op.JL || c.op === Op.LINE) {
          const newRev = revMap.get(c.rev) ?? c.rev;
          if (newRev > newMaxRev) {
            newMaxRev = newRev;
          }
          return c.set('rev', newRev);
        }
        return c;
      })
      .toList();
    const newValueOf = `R${[...revMap.entries()]}`;
    const newCode = this.newCode(newInstList, newValueOf);
    return [newCode, newMaxRev];
  }

  private newCode(instList: List<Inst>, newValueOf: string): Code {
    const newStr = this.__valueOf + '\0' + newValueOf;
    // We want bitwise operations.
    // eslint-disable-next-line no-bitwise
    const newHash = (this.__hash * 23 + hash(newValueOf)) & 0x7fffffff;
    return new Code(instList, newHash, newStr);
  }
}

// Export for testing purpose.
export const executeCache: LRUWithStats = new LRU(100);

type LineLogProps = {
  /** Core state: instructions. The array index type is `Pc`. */
  code: Code;
  /** Maximum rev tracked (inclusive). */
  maxRev: Rev;
};

const LineLogRecord = Record<LineLogProps>({
  code: new Code(),
  maxRev: 0 as Rev,
});
type LineLogRecord = RecordOf<LineLogProps>;

/**
 * `LineLog` is a data structure that tracks linear changes to a single text
 * file. Conceptually similar to a list of texts like `string[]`, with extra
 * features suitable for stack editing:
 * - Calculate the "blame" of the text of a given version efficiently.
 * - Edit lines or trunks in a past version, and affect future versions.
 * - List all lines that ever existed with each line annotated, like
 *   a unified diff, but for all versions, not just 2 versions.
 *
 * Internally, `LineLog` is a byte-code interpreter that runs a program to
 * emit lines. Changes are done by patching in new byte-codes. There are
 * no traditional text patch involved. No operations would cause merge
 * conflicts. See https://sapling-scm.com/docs/internals/linelog for more
 * details.
 *
 * This implementation of `LineLog` uses immutable patterns.
 * Write operations return new `LineLog`s.
 */
class LineLog extends SelfUpdate<LineLogRecord> {
  constructor(props?: {code?: Code; maxRev?: Rev}) {
    const record = LineLogRecord(props);
    super(record);
  }

  get maxRev(): Rev {
    return this.inner.maxRev;
  }

  get code(): Code {
    return this.inner.code;
  }

  /**
   * Edit chunk. Replace line `a1` (inclusive) to `a2` (exclusive) in rev
   * `aRev` with `bLines`. `bLines` are considered introduced by `bRev`.
   * If `bLines` is empty, the edit is a deletion. If `a1` equals to `a2`,
   * the edit is an insertion. Otherwise, the edit is a modification.
   *
   * While this function does not cause conflicts or error out, not all
   * editings make practical sense. The callsite might want to do some
   * extra checks to ensure the edit is meaningful.
   *
   * `aLinesCache` is optional. If provided, then `editChunk` will skip a
   * `checkOutLines` call and modify `aLinesCache` *in place* to reflect
   * the edit. It is used by `recordText`.
   */
  editChunk(
    aRev: Rev,
    a1: LineIdx,
    a2: LineIdx,
    bRev: Rev,
    bLines: string[],
    aLinesCache?: LineInfo[],
  ): LineLog {
    const aLinesMutable = aLinesCache != null;
    const aLinesInfo: [LineInfo[], true] | [Readonly<LineInfo[]>, false] = aLinesMutable
      ? [aLinesCache, true]
      : [this.checkOutLines(aRev), false];
    const newCode = this.code.editChunk(aRev, a1, a2, bRev, bLines, aLinesInfo);
    const newMaxRev = Math.max(bRev, this.maxRev);
    return new LineLog({code: newCode, maxRev: newMaxRev});
  }

  /**
   * Rewrite `rev` to `mapping[rev] ?? rev`.
   * This can be useful for reordering, folding, or insertion.
   *
   * Note: There are no checks about whether the reordering is
   * meaningful or not. The callsite is responsible to perform
   * a dependency check and avoid troublesome reorders like
   * moving a change to before its dependency.
   */
  remapRevs(revMap: Map<Rev, Rev>): LineLog {
    const [newCode, newMaxRev] = this.code.remapRevs(revMap);
    return new LineLog({code: newCode, maxRev: newMaxRev});
  }

  /**
   * Calculate the dependencies of revisions.
   * For example, `{5: [3, 1]}` means rev 5 depends on rev 3 and rev 1.
   *
   * Based on LineLog, which could be different from traditional textual
   * context-line dependencies. LineLog dependency is to prevent
   * "malformed cases" [1] when following the dependency to `remapRevs`.
   * Practically, LineLog might allow reorder cases that would be
   * disallowed by traditional context-line dependencies. See tests
   * for examples.
   *
   * [1]: Malformed cases are when nested blocks (insertions or deletions)
   *      might be skipped incorrectly. The outer block says "skip" and the
   *      inner block does not want to "skip" but is still skipped since it
   *      is skipped altogher with the outer block. See also section 0.4
   *      and 0.5 in D3628440.
   */
  @cached({cacheSize: 1000})
  calculateDepMap(): Readonly<Map<Rev, Set<Rev>>> {
    const depMap = new Map<Rev, Set<Rev>>();
    const addDep = (child: Rev, parent: Rev) => {
      if (child > parent) {
        if (!depMap.has(child)) {
          depMap.set(child, new Set());
        }
        depMap.get(child)?.add(parent);
      }
    };

    // Figure out the dependencies by following the LineLog instructions.
    //
    // How does it work? First, insertions and deletions in linelog form
    // tree structures. For example:
    //
    //    +---- Insertion (rev 1)
    //    |     Line 1
    //    |                    ----+ Deletion (rev 4)
    //    |     Line 2             |
    //    | +-- Insertion (rev 2)  |
    //    | |   Line 3             |
    //    | |                  --+ | Deletion (rev 3)
    //    | |   Line 4           | |
    //    | +--                  | |
    //    |     Line 5           | |
    //    |                    --+ |
    //    |     Line 6             |
    //    |                    ----+
    //    |     Line 7
    //    +----
    //
    // Note interleaved insertions do not happen. For example, this does not
    // happen:
    //
    //    +---- Insertion (rev 1)
    //    |     Line 1
    //    | +-- Insertion (rev 2)
    //    | |   Line 2
    //    +-|--
    //      |   Line 3
    //      +--
    //
    // Similarly, interleaved deletions do not happen. However, insertions
    // might interleave with deletions, as shown above.
    //
    // We track the current insertion rev and deletion rev using 2 stacks,
    // when we see a new insertion block, or deletion block, we add two
    // dependencies:
    // - The inner rev depends on the outer insertion rev.
    // - The outer deletion rev (if present) depends on the inner rev.
    //
    // Let's look at how this is done at the instruction level. First, look at
    // the instructions generated by editChunk:
    //
    //      a2Pc: ...
    //            ...
    //     start: JL brev b2Pc
    //            ...
    //      b2Pc: JGE brev a2Pc
    //          : <a1 Inst>
    //       end: J a1Pc+1
    //
    // JL is used for insertion, JGE is used for deletion. We then use them to
    // manipulate the insStack and delStack:
    //
    // insStack:
    //
    //    - On "start: JL brev b2Pc":
    //      Do not follow the JL jump.
    //      Mark brev as dependent on the outer insertion.
    //      Mark the outer deletion as dependent on this brev.
    //      Push {rev, b2Pc} to insStack.
    //    - When pc is b2Pc, pop insStack.
    //
    // delStack:
    //
    //    - On "b2Pc: JGE brev a2Pc":
    //      Do not follow the JGE jump.
    //      Mark brev as dependent on the outer insertion.
    //      Mark the outer deletion as dependent on this brev.
    //      Push {rev, a2Pc} to delStack.
    //    - When pc is a2Pc, pop delStack.
    //
    // You might have noticed that we don't use the revs in LINE instructions
    // at all. This is because that LINE rev always matches its JL rev in this
    // implementation. In other words, the "rev" in LINE instruction is
    // redundant as it can be inferred from JL, with an insStack. Note in the
    // original C implementation of LineLog the LINE rev can be different from
    // the JL rev, to deal with merges while maintaining a linear history.
    type Frame = {rev: Rev; endPc: Pc};
    const insStack: Frame[] = [{rev: 0, endPc: -1}];
    const delStack: Frame[] = [];
    const markDep = (rev: Rev) => {
      const ins = insStack.at(-1);
      if (ins !== undefined) {
        addDep(rev, ins.rev);
      }
      const del = delStack.at(-1);
      if (del !== undefined) {
        addDep(del.rev, rev);
      }
    };

    const codeList = this.inner.code;
    let pc = 0;
    let patience = codeList.getSize() * 2;
    while (patience > 0) {
      if (insStack.at(-1)?.endPc === pc) {
        insStack.pop();
      }
      if (delStack.at(-1)?.endPc === pc) {
        delStack.pop();
      }
      const code = unwrap(codeList.get(pc));
      switch (code.op) {
        case Op.END:
          patience = -1;
          break;
        case Op.LINE:
          pc += 1;
          break;
        case Op.J:
          pc = code.pc;
          break;
        case Op.JGE:
          markDep(code.rev);
          delStack.push({rev: code.rev, endPc: code.pc});
          pc += 1;
          break;
        case Op.JL:
          markDep(code.rev);
          insStack.push({rev: code.rev, endPc: code.pc});
          pc += 1;
          break;
        default:
          assert(false, 'bug: unknown code');
      }
      patience -= 1;
    }
    if (patience === 0) {
      assert(false, 'bug: code does not end in time');
    }

    return depMap;
  }

  /**
   * Interpret the bytecodes with the given revision range.
   * Used by `checkOut`.
   */
  @cached({cache: executeCache, cacheSize: 1000})
  execute(
    startRev: Rev,
    endRev: Rev = startRev,
    present?: {[pc: number]: boolean},
  ): Readonly<LineInfo[]> {
    const rev = endRev;
    const lines: LineInfo[] = [];
    let pc = 0;
    let patience = this.code.getSize() * 2;
    const deleted = present == null ? () => false : (pc: Pc) => !present[pc];
    while (patience > 0) {
      const code = unwrap(this.code.get(pc));
      switch (code.op) {
        case Op.END:
          lines.push({data: '', rev: 0, pc, deleted: deleted(pc)});
          patience = -1;
          break;
        case Op.LINE:
          lines.push({data: code.data, rev: code.rev, pc, deleted: deleted(pc)});
          pc += 1;
          break;
        case Op.J:
          pc = code.pc;
          break;
        case Op.JGE:
          if (startRev >= code.rev) {
            pc = code.pc;
          } else {
            pc += 1;
          }
          break;
        case Op.JL:
          if (rev < code.rev) {
            pc = code.pc;
          } else {
            pc += 1;
          }
          break;
        default:
          assert(false, 'bug: unknown code');
      }
      patience -= 1;
    }
    if (patience === 0) {
      assert(false, 'bug: code does not end in time');
    }
    return lines;
  }

  /**
   * Flatten lines. Each returned line is associated with a set
   * of `Rev`s, meaning that line is present in those `Rev`s.
   *
   * The returned lines can be useful to figure out file contents
   * after reordering, folding commits. It can also provide a view
   * similar to `absorb -e FILE` to edit all versions of a file in
   * a single view.
   */
  @cached({cacheSize: 1000})
  public flatten(): Readonly<FlattenLine[]> {
    const result: FlattenLine[] = [];

    // See the comments in calculateDepMap for what the stacks mean.
    //
    // The flatten algorithm works as follows:
    // - For each line, we got an insRev (insStack.at(-1).rev), and a
    //   delRev (delStack.at(-1)?.rev ?? maxRev + 1), meaning the rev
    //   attached to the innermost insertion or deletion blocks,
    //   respectively.
    // - That line is then present in insRev .. delRev (exclusive) revs.
    //
    // This works because:
    // - The blocks are nested in order:
    //    - For nested insertions, the nested one must have a larger rev, and
    //      lines inside the nested block are only present starting from the
    //      larger rev.
    //    - For nested deletions, the nested one must have a smaller rev, and
    //      lines inside the nested block are considered as deleted by the
    //      smaller rev.
    //    - For interleaved insertion and deletions, insertion rev and deletion
    //      rev are tracked separately so their calculations are independent
    //      from each other.
    // - Linelog tracks linear history, so (insRev, delRev) can be converted to
    //   a Set<Rev>.
    type Frame = {rev: Rev; endPc: Pc};
    const insStack: Frame[] = [{rev: 0, endPc: -1}];
    const delStack: Frame[] = [];
    const maxDelRev = this.maxRev + 1;
    const getCurrentRevs = (): Readonly<Set<Rev>> => {
      const insRev = insStack.at(-1)?.rev ?? 0;
      const delRev = delStack.at(-1)?.rev ?? maxDelRev;
      return revRangeToSet(insRev, delRev);
    };

    const codeList = this.inner.code;
    let pc = 0;
    let patience = codeList.getSize() * 2;
    let currentRevs = getCurrentRevs();
    while (patience > 0) {
      if (insStack.at(-1)?.endPc === pc) {
        insStack.pop();
        currentRevs = getCurrentRevs();
      }
      if (delStack.at(-1)?.endPc === pc) {
        delStack.pop();
        currentRevs = getCurrentRevs();
      }
      const code = unwrap(codeList.get(pc));
      switch (code.op) {
        case Op.END:
          patience = -1;
          break;
        case Op.LINE:
          result.push({data: code.data, revs: currentRevs});
          pc += 1;
          break;
        case Op.J:
          pc = code.pc;
          break;
        case Op.JGE:
          delStack.push({rev: code.rev, endPc: code.pc});
          currentRevs = getCurrentRevs();
          pc += 1;
          break;
        case Op.JL:
          insStack.push({rev: code.rev, endPc: code.pc});
          currentRevs = getCurrentRevs();
          pc += 1;
          break;
        default:
          assert(false, 'bug: unknown code');
      }
      patience -= 1;
    }
    if (patience === 0) {
      assert(false, 'bug: code does not end in time');
    }

    return result;
  }

  /**
   * Checkout the lines of the given revision `rev`.
   *
   * If `start` is not `null`, checkout a revision range. For example,
   * if `start` is 0, and `rev` is `this.maxRev`, `this.lines` will
   * include all lines ever existed in all revisions.
   *
   * @returns Content of the specified revision.
   */
  public checkOutLines(rev: Rev, start: Rev | null = null): Readonly<LineInfo[]> {
    // eslint-disable-next-line no-param-reassign
    rev = Math.min(rev, this.maxRev);
    let lines = this.execute(rev);
    if (start !== null) {
      // Checkout a range, including deleted revs.
      const present: {[key: number]: boolean} = {};
      lines.forEach(l => {
        present[l.pc] = true;
      });

      // Go through all lines again. But do not skip chunks.
      lines = this.execute(start, rev, present);
    }
    return lines;
  }

  /** Checkout the content of the given rev. */
  public checkOut(rev: Rev): string {
    const lines = this.checkOutLines(rev);
    const content = lines.map(l => l.data).join('');
    return content;
  }

  /**
   * Edit LineLog to match the content of `text`.
   * This might affect `rev`s that are >= `rev` in the stack.
   * Previous revisions won't be affected.
   *
   * @param text Content to match.
   * @param rev Revision to to edit (in-place). If not set, append a new revision.
   * @returns A new `LineLog` with the change.
   */
  @cached({cacheSize: 1000})
  public recordText(text: string, rev: Rev | null = null): LineLog {
    // rev to edit from, and rev to match 'text'.
    const [aRev, bRev] = rev != null ? [rev, rev] : [this.maxRev, this.maxRev + 1];
    const b = text;

    const aLineInfos = [...this.checkOutLines(aRev)];
    const bLines = splitLines(b);
    const aLines = aLineInfos.map(l => l.data);
    aLines.pop(); // Drop the last END empty line.
    const blocks = diffLines(aLines, bLines);
    // eslint-disable-next-line @typescript-eslint/no-this-alias
    let log: LineLog = this;

    blocks.reverse().forEach(([a1, a2, b1, b2]) => {
      log = log.editChunk(aRev, a1, a2, bRev, bLines.slice(b1, b2), aLineInfos);
    });

    // This is needed in case editChunk is not called (no difference).
    const newMaxRev = Math.max(bRev, log.maxRev);

    // Populate cache for checking out bRev.
    const newLog = new LineLog({code: log.code, maxRev: newMaxRev});
    executeCache.set(List([newLog, bRev]), aLineInfos);

    return newLog;
  }
}

/**
 * Calculate the line differences. For performance, this function only
 * returns the line indexes for different chunks. The line contents
 * are not returned.
 *
 * @param aLines lines on the "a" side.
 * @param bLines lines on the "b" side.
 * @returns A list of `(a1, a2, b1, b2)` tuples for the line ranges that
 * are different between "a" and "b".
 */
function diffLines(aLines: string[], bLines: string[]): [LineIdx, LineIdx, LineIdx, LineIdx][] {
  // Avoid O(string length) comparison.
  const [aList, bList] = stringsToInts([aLines, bLines]);

  // Skip common prefix and suffix.
  let aLen = aList.length;
  let bLen = bList.length;
  const minLen = Math.min(aLen, bLen);
  let commonPrefixLen = 0;
  while (commonPrefixLen < minLen && aList[commonPrefixLen] === bList[commonPrefixLen]) {
    commonPrefixLen += 1;
  }
  while (aLen > commonPrefixLen && bLen > commonPrefixLen && aList[aLen - 1] === bList[bLen - 1]) {
    aLen -= 1;
    bLen -= 1;
  }
  aLen -= commonPrefixLen;
  bLen -= commonPrefixLen;

  // Run the diff algorithm.
  const blocks: [LineIdx, LineIdx, LineIdx, LineIdx][] = [];
  let a1 = 0;
  let b1 = 0;

  function isCommon(aIndex: number, bIndex: number) {
    return aList[aIndex + commonPrefixLen] === bList[bIndex + commonPrefixLen];
  }

  function foundSequence(n: LineIdx, a2: LineIdx, b2: LineIdx) {
    if (a1 !== a2 || b1 !== b2) {
      blocks.push([
        a1 + commonPrefixLen,
        a2 + commonPrefixLen,
        b1 + commonPrefixLen,
        b2 + commonPrefixLen,
      ]);
    }
    a1 = a2 + n;
    b1 = b2 + n;
  }

  diffSequences(aLen, bLen, isCommon, foundSequence);
  foundSequence(0, aLen, bLen);

  return blocks;
}

/**
 * Split lines by `\n`. Preserve the end of lines.
 */
function splitLines(s: string): string[] {
  let pos = 0;
  let nextPos = 0;
  const result = [];
  while (pos < s.length) {
    nextPos = s.indexOf('\n', pos);
    if (nextPos === -1) {
      nextPos = s.length - 1;
    }
    result.push(s.slice(pos, nextPos + 1));
    pos = nextPos + 1;
  }
  return result;
}

/**
 * Make strings with the same content use the same integer
 * for fast comparison.
 */
function stringsToInts(linesArray: string[][]): number[][] {
  // This is similar to diff-match-patch's diff_linesToChars_ but is not
  // limited to 65536 unique lines.
  const lineMap = new Map<string, number>();
  return linesArray.map(lines =>
    lines.map(line => {
      const existingId = lineMap.get(line);
      if (existingId != null) {
        return existingId;
      } else {
        const id = lineMap.size;
        lineMap.set(line, id);
        return id;
      }
    }),
  );
}

/** Turn (3, 6) to Set([3, 4, 5]). */
const revRangeToSet = cached(
  (startRev, endRev: Rev): Readonly<Set<Rev>> => {
    const result = new Set<Rev>();
    for (let rev = startRev; rev < endRev; rev++) {
      result.add(rev);
    }
    return result;
  },
  {cacheSize: 1000},
);

export {LineLog};
export type {FlattenLine, Rev, LineIdx, LineInfo};
