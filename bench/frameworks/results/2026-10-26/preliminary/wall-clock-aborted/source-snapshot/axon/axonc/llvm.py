"""LLVM IR backend: Axon graph -> LLVM IR -> native code (the rustc pipeline).

Lowering uses allocas for every binder and lets LLVM's mem2reg/SROA promote
them to SSA registers, as clang and rustc do. Parallel `sum`/`arr` regions are
outlined into separate functions with an explicit context struct (closure
conversion) and dispatched by the Axon runtime's per-region kernel workers.

Memory: arrays are { ptr, len, owned }. Ownership moves along a uniqueness
discipline decided per scope by `consume_plan`: an array variable whose every
control-flow path uses it at most once as a whole (passed to a call, updated
with `set`, returned, stored into a loop state) is *moved* at that use, and
`set` then updates in place. Whatever still owns a buffer at scope exit frees
it. Arrays of records are laid out struct-of-arrays inside one buffer, fields
ordered by size; the program never sees the layout.

Proven facts reach LLVM as `llvm.assume` (index ranges, dereferenceable
buffers), so the optimizer can speculate and vectorize what the checker proved.
"""
from __future__ import annotations
import struct
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple
from . import syntax as S
from . import vectorize as VZ
from . import ranges as Ranges

ARR = "%ax_arr"

@dataclass
class Val:
    name: str
    ty: str
    def __str__(self): return f"{self.ty} {self.name}"

@dataclass
class Slot:
    ptr: str
    ty: str
    own_ptr: Optional[str] = None            # i64 flag for array variables
    consume: Set[int] = field(default_factory=set)   # node ids at which this variable is moved
    axty: object = None
    borrowed: bool = False
    field_consume: dict = field(default_factory=dict)
    resource_live: Optional[str] = None

def lty(t) -> str:
    if isinstance(t,S.TRef):return "{ i64, i64 }"
    if isinstance(t,S.TFn):return "ptr"
    if t == S.I64: return "i64"
    if t == S.F64: return "double"
    if t == S.BOOL: return "i1"
    if t == S.U8: return "i8"
    if t == S.I32: return "i32"
    if t == S.PTR: return "ptr"
    if t == S.UNIT: return "i8"  # unit values have no payload; function ABI returns void
    if isinstance(t, S.TArr): return ARR
    if isinstance(t, S.TRec): return "{ " + ", ".join(lty(ft) for _, ft in t.fields) + " }"
    raise TypeError(t)

def scalar_size(t) -> int:
    if isinstance(t,S.TRef):return 16
    if isinstance(t, S.TArr): return 24
    if isinstance(t, S.TRec):
        offset, alignment = 0, 1
        for _, ft in t.fields:
            size = scalar_size(ft); align = min(8, size)
            offset = (offset + align - 1) // align * align + size
            alignment = max(alignment, align)
        return max(1,(offset + alignment - 1) // alignment * alignment)
    if t == S.I32: return 4
    return 1 if t in (S.BOOL, S.U8, S.UNIT) else 8

def return_type(t): return "void" if t == S.UNIT else lty(t)

def elem_size(t: S.TArr) -> int:
    if isinstance(t.elem, S.TRec): return max(1,sum(scalar_size(ft) for _, ft in t.elem.fields))
    return scalar_size(t.elem)

def rec_layout(rt: S.TRec) -> List[Tuple[int, str, int, int]]:
    """(field index, llvm type, size, prefix bytes per element) in layout order: big fields first."""
    order = sorted(range(len(rt.fields)), key=lambda k: -scalar_size(rt.fields[k][1]))
    out, prefix = [], 0
    for k in order:
        ft = rt.fields[k][1]
        out.append((k, lty(ft), scalar_size(ft), prefix))
        prefix += scalar_size(ft)
    return out

def zero(t) -> str:
    if isinstance(t,S.TRef):return 'zeroinitializer'
    if isinstance(t,S.TFn):return "null"
    if isinstance(t, S.TArr): return 'zeroinitializer'
    if t == S.F64: return "0.0"
    if isinstance(t, S.TRec): return "{ " + ", ".join(f"{lty(ft)} {zero(ft)}" for _, ft in t.fields) + " }"
    return "0"

def managed(t):
    return isinstance(t, S.TArr) or isinstance(t, S.TRec) and any(managed(ft) for _, ft in t.fields)

def fconst(v: float) -> str:
    return "0x" + struct.pack(">d", v).hex().upper()

def cstr(s: str) -> Tuple[str, int]:
    b = s.encode("utf-8") + b"\0"
    return "".join(chr(ch) if 32 <= ch < 127 and ch not in (34, 92) else f"\\{ch:02X}" for ch in b), len(b)

# ---------------------------------------------------------------- uniqueness analysis
@dataclass(frozen=True)
class Summary:
    c: int          # consumptions on this path
    r: bool         # any read on this path
    bad: bool       # a read after a consumption, or two consumptions
    ids: frozenset  # consuming node ids

def seq(a: List[Summary], b: List[Summary]) -> List[Summary]:
    out = {}
    for x in a:
        for y in b:
            s = Summary(x.c + y.c, x.r or y.r, x.bad or y.bad or (x.c > 0 and (y.r or y.c > 0)), x.ids | y.ids)
            key = (s.c, s.r, s.bad)
            out[key] = Summary(s.c, s.r, s.bad, out[key].ids | s.ids) if key in out else s
    return list(out.values())

EMPTY = [Summary(0, False, False, frozenset())]
def READ(): return [Summary(0, True, False, frozenset())]
def downgrade(a: List[Summary]) -> List[Summary]:
    return [Summary(0, x.r or x.c > 0, x.bad, frozenset()) for x in a]

def paths(e: S.Node, name: str, as_read: bool = False, projection=None) -> List[Summary]:
    def walk(child, binding, as_read=False): return paths(child,binding,as_read,projection)
    if isinstance(e, S.Var):
        if e.name != name: return EMPTY
        return READ() if as_read else [Summary(1, False, False, frozenset([id(e)]))]
    if isinstance(e, (S.Num, S.Flt, S.BoolLit, S.Str, S.Byte)): return EMPTY
    if isinstance(e, S.Bin):
        l, r = walk(e.left, name), walk(e.right, name)
        return seq(l, r) + l if e.op in ("&&", "||") else seq(l, r)
    if isinstance(e, S.Un): return walk(e.expr, name)
    if isinstance(e, S.Cast): return walk(e.expr, name)
    if isinstance(e, S.Len): return walk(e.expr, name, as_read=True)
    if isinstance(e, S.Index): return seq(walk(e.arr, name, as_read=True), walk(e.idx, name))
    if isinstance(e, S.Field) and isinstance(e.expr,S.Var) and e.expr.name==name and projection is not None:
        if e.name!=projection:return EMPTY
        return READ() if as_read or not managed(e.ty) else [Summary(1,False,False,frozenset([id(e)]))]
    if isinstance(e, S.Field): return walk(e.expr, name, as_read=as_read or not managed(e.ty))
    if isinstance(e, S.RecLit):
        out = EMPTY
        for _, v in e.fields: out = seq(out, walk(v, name))
        return out
    if isinstance(e, S.With):
        out = walk(e.expr, name)
        for _, v in e.fields: out = seq(out, walk(v, name))
        return out
    if isinstance(e, S.Call):
        if getattr(e, "builtin", None) == "data": return walk(e.args[0], name, as_read=True)
        if getattr(e, "builtin", None) == "addr": return EMPTY
        if getattr(e, "builtin", None) == "set":
            a, i, v = e.args
            out = seq(walk(i, name), walk(v, name))
            return seq(out, walk(a, name))                  # the array is consumed last, after index and value
        if getattr(e, "builtin", None) == "serve": return EMPTY
        # concat and user calls consume whole-array arguments after all arguments are evaluated
        out, deferred = EMPTY, EMPTY
        for argument_index,a in enumerate(e.args):
            if argument_index in getattr(e,'borrowed_args',set()):
                out=seq(out,walk(a,name,as_read=True));continue
            if isinstance(a, S.Var) and a.name == name: deferred = seq(deferred, walk(a, name))
            else: out = seq(out, walk(a, name))
        return seq(out, deferred)                            # whole-array arguments are consumed by the call
    if isinstance(e, S.Let): return seq(walk(e.value, name), walk(e.body, name))
    if isinstance(e, S.If): return seq(walk(e.cond, name), walk(e.then, name) + walk(e.els, name))
    if isinstance(e, (S.Sum, S.ArrC)):
        return seq(seq(walk(e.lo, name), walk(e.hi, name)), downgrade(walk(e.body, name)))
    if isinstance(e, S.Check): return seq(walk(e.pred, name), walk(e.body, name))
    if isinstance(e, S.Loop):
        out = EMPTY
        for x in e.inits: out = seq(out, walk(x, name))
        inner = walk(e.cond, name)
        for x in e.nexts: inner = seq(inner, walk(x, name))
        inner = seq(inner, walk(e.yld, name))
        return seq(out, downgrade(inner))
    raise TypeError(type(e))

def consume_plan(exprs: List[S.Node], name: str, projection=None) -> Set[int]:
    """Node ids where `name` may be moved, or empty if the variable must stay borrowed everywhere."""
    out = EMPTY
    for e in exprs: out = seq(out, paths(e, name, projection=projection))
    if any(s.bad for s in out): return set()
    ids: Set[int] = set()
    for s in out: ids |= s.ids
    return ids


def field_consume_plan(exprs, name, ty):
    if not isinstance(ty,S.TRec):return {}
    return {field:consume_plan(exprs,name,projection=field) for field,child in ty.fields if managed(child)}

# ---------------------------------------------------------------- IR builder
class Block:
    def __init__(self, label: str):
        self.label = label; self.lines: List[str] = []

class FuncBuilder:
    def __init__(self, header: str):
        self.header = header
        self.allocas: List[str] = []
        self.blocks: List[Block] = []
        self.n = 0
        self.cur = self.block("entry")
    def tmp(self) -> str:
        self.n += 1; return f"%t{self.n}"
    def label(self, base: str) -> str:
        self.n += 1; return f"{base}{self.n}"
    def block(self, label: str) -> Block:
        b = Block(label); self.blocks.append(b); self.cur = b; return b
    def emit(self, line: str): self.cur.lines.append("  " + line)
    def ins(self, text: str, ty: str) -> Val:
        t = self.tmp(); self.emit(f"{t} = {text}"); return Val(t, ty)
    def alloca(self, ty: str) -> str:
        self.n += 1; p = f"%a{self.n}"; self.allocas.append(f"  {p} = alloca {ty}"); return p
    def store(self, v: Val, ptr: str): self.emit(f"store {v}, ptr {ptr}")
    def load(self, ty: str, ptr: str) -> Val: return self.ins(f"load {ty}, ptr {ptr}", ty)
    def br(self, label: str): self.emit(f"br label %{label}")
    def cbr(self, c: Val, a: str, b: str): self.emit(f"br i1 {c.name}, label %{a}, label %{b}")
    def render(self) -> str:
        out = [self.header + " {"]
        for i, b in enumerate(self.blocks):
            out.append(f"{b.label}:")
            if i == 0: out.extend(self.allocas)
            out.extend(b.lines)
        out.append("}")
        return "\n".join(out)

class LLVMGen:
    def __init__(self, fns: List[S.Fn], fn_hashes: Dict[str, str], triple: str, assume: bool = True, vectorize: bool = True, ranges: bool = True, literal_inline: bool = True):
        self.fns = fns; self.fn_hashes = fn_hashes; self.triple = triple
        self.assume = assume; self.vectorize = vectorize
        self.fn_map = {f.name: f for f in fns}
        self.vector_sums: List[dict] = []
        self.vector_arrays: List[dict] = []
        self.funcs: List[str] = []
        self.strings: Dict[str, str] = {}
        self.byte_consts: Dict[bytes, str] = {}
        self.par_regions: List[dict] = []
        self.fb: FuncBuilder = None
        self.mode = "par"; self.fn: S.Fn = None
        self.region_k = 0
        self.value_ranges = Ranges.analyze(fns) if ranges else {}
        self.bounded_remainders = []
        self.overflow_checks = []
        self.resource_finalizers = {}
        self.literal_inlined = set()
        self.small_pure_inlined = set()
        if literal_inline:
            from .inline import node_count, is_recursive
            def visit(node):
                if isinstance(node,S.Call) and any(isinstance(a,S.Str) for a in node.args):
                    target=self.fn_map.get(node.name)
                    if target and not (target.effect or target.exported or target.external) and node_count(target.body)<=160 and not is_recursive(target,self.fn_map):
                        self.literal_inlined.add(target.name)
                for child in S.children(node):visit(child)
            for function in fns:
                if not function.external:visit(function.body)
            # Checked operations and owned-return cleanup inflate LLVM cost
            # estimates. Small Axon helpers should expose their facts and
            # lifetimes to the caller before LLVM applies its cost model.
            for function in fns:
                if function.name!='main' and not (function.effect or function.exported or function.external) and node_count(function.body)<=160 and not is_recursive(function,self.fn_map):
                    self.small_pure_inlined.add(function.name)

    # ------------------------------------------------------------ module
    def string(self, s: str) -> str:
        if s not in self.strings: self.strings[s] = f"@.str.{len(self.strings) + 1}"
        return self.strings[s]
    def bytes_const(self, b: bytes) -> str:
        if b not in self.byte_consts: self.byte_consts[b] = f"@.bytes.{len(self.byte_consts) + 1}"
        return self.byte_consts[b]

    def program(self) -> str:
        for f in self.fns:
            if f.external: continue
            for mode in (("seq",) if f.effect or f.exported else ("par", "seq")):
                self.function(f, mode)
        self.main_wrapper()
        head = ['source_filename = "axon"',f'target triple = "{self.triple}"', "",
                f"{ARR} = type {{ ptr, i64, i64 }}", "",
                "@ax_runtime_state = internal global [256 x i64] zeroinitializer, align 16",
                "declare double @llvm.sqrt.f64(double)", "declare void @llvm.assume(i1)",
                f"declare <{VZ.K} x double> @llvm.sqrt.v{VZ.K}f64(<{VZ.K} x double>)",
                f"declare i1 @llvm.vector.reduce.or.v{VZ.K}i1(<{VZ.K} x i1>)",
                f"declare i64 @llvm.vector.reduce.add.v{VZ.K}i64(<{VZ.K} x i64>)",
                f"declare double @llvm.vector.reduce.fadd.v{VZ.K}f64(double, <{VZ.K} x double>)",
                "declare void @llvm.memcpy.p0.p0.i64(ptr, ptr, i64, i1)", "declare i64 @llvm.ctlz.i64(i64, i1)", 'declare i64 @llvm.bswap.i64(i64)', ""]
        head += ['declare { i64, i1 } @llvm.s'+op+'.with.overflow.i64(i64, i64)' for op in ('add','sub','mul')]
        head += [f'declare {{ <{VZ.K} x i64>, <{VZ.K} x i1> }} @llvm.s'+op+f'.with.overflow.v{VZ.K}i64(<{VZ.K} x i64>, <{VZ.K} x i64>)' for op in ('add','sub','mul')]
        k=VZ.ARRAY_LANES
        head += [f'declare <{k} x double> @llvm.sqrt.v{k}f64(<{k} x double>)', f'declare i1 @llvm.vector.reduce.or.v{k}i1(<{k} x i1>)']
        head += [f'declare {{ <{k} x i64>, <{k} x i1> }} @llvm.s{op}.with.overflow.v{k}i64(<{k} x i64>, <{k} x i64>)' for op in ('add','sub','mul')]
        from .kernel_workers import assembly
        head += assembly(self.triple)
        for f in self.fns:
            if f.external:
                pts = [lty(p.ty) for p in f.params] + (["..."] if f.variadic else [])
                head.append(f"declare {return_type(f.ret)} @{f.name}({', '.join(pts)})")
        for s, name in self.strings.items():
            body, n = cstr(s)
            head.append(f'{name} = private unnamed_addr constant [{n} x i8] c"{body}"')
        for b, name in self.byte_consts.items():
            body = "".join(chr(ch) if 32 <= ch < 127 and ch not in (34, 92) else f"\\{ch:02X}" for ch in b)
            head.append(f'{name} = private unnamed_addr constant [{len(b)} x i8] c"{body}"')
        head.append("")
        return "\n".join(head) + "\n\n".join(self.funcs) + "\n"

    def fname(self, f: S.Fn, mode: str) -> str:
        if f.external or f.exported: return f"@{f.name}"
        if f.effect: mode = "seq"
        return f"@ax_{f.name}__{mode}"

    def function(self, f: S.Fn, mode: str):
        params = ", ".join(f"{lty(p.ty)} %p_{p.name}" for p in f.params)
        fb = FuncBuilder(f"; {f.name}  graph:{self.fn_hashes.get(f.name, '')[:16]}\n"
                         f"define {'internal ' if not f.exported else ''}{return_type(f.ret)} {self.fname(f, mode)}({params}) {'alwaysinline ' if f.name in (self.literal_inlined | self.small_pure_inlined) else ''}\"no-builtins\"")
        self.fb, self.mode, self.fn = fb, mode, f
        env: Dict[str, Slot] = {}
        for p in f.params:
            ty = lty(p.ty); ptr = fb.alloca(ty); v = Val(f"%p_{p.name}", ty); fb.store(v, ptr)
            s = Slot(ptr, ty, axty=p.ty)
            if isinstance(p.ty, S.TArr):
                s.own_ptr = fb.alloca("i64")
                fb.store(fb.ins(f"extractvalue {v}, 2", "i64"), s.own_ptr)   # the caller may have moved it to us
                s.consume = consume_plan([f.body], p.name)
                self.assume_array(v, p.ty)
            elif managed(p.ty):
                if not f.effect and not f.exported and not p.owned:
                    s.borrowed = True
                else:
                    s.own_ptr = fb.alloca('i64'); fb.store(Val('1','i64'), s.own_ptr)
                    s.consume = consume_plan([f.body], p.name)
                    s.field_consume = field_consume_plan([f.body],p.name,p.ty)
            env[p.name] = s
        for s, carrier in f.size_binders:
            a = fb.load(ARR, env[carrier].ptr)
            n = fb.ins(f"extractvalue {a}, 1", "i64")
            ptr = fb.alloca("i64"); fb.store(n, ptr); env[s] = Slot(ptr, "i64", axty=S.I64)
        v, own = self.gen(f.body, env, par=(mode == "par"))
        if isinstance(f.ret, S.TArr) and any(env[p.name].borrowed for p in f.params):
            # A borrowed record's child cannot escape its caller's lifetime.
            v = self.owning_value(v, own, f.ret)
            own = fb.ins(f'extractvalue {v}, 2', 'i64')
        self.free_scope([env[p.name] for p in f.params if managed(p.ty)], own if isinstance(f.ret, S.TArr) else None)
        if f.ret == S.UNIT: fb.emit("ret void")
        elif isinstance(f.ret, S.TArr):
            fb.emit(f"ret {self.with_own(v, own)}")
        else:
            fb.emit(f"ret {v}")
        self.funcs.append(fb.render())

    def main_wrapper(self):
        m = next(f for f in self.fns if f.name == "main")
        fb = FuncBuilder("define i32 @main(i32 %argc, ptr %argv)")
        self.fb, self.mode, self.fn = fb, "par", m
        argc64 = fb.ins("zext i32 %argc to i64", "i64")
        endargv = fb.ins(f"getelementptr ptr, ptr %argv, i64 {argc64.name}", "ptr")
        envp = fb.ins(f"getelementptr ptr, {endargv}, i64 1", "ptr")
        fb.emit(f"call void @ax_runtime_init({envp})")
        argc_slot = fb.ins('getelementptr i8, ptr @ax_runtime_state, i64 40','ptr')
        argv_slot = fb.ins('getelementptr i8, ptr @ax_runtime_state, i64 48','ptr')
        fb.store(argc64,argc_slot.name); fb.store(Val('%argv','ptr'),argv_slot.name)
        n = len(m.params)
        ok = Val('true','i1') if n == 0 else fb.ins(f"icmp eq i32 %argc, {n + 1}", "i1")
        parse, usage = fb.label("parse"), fb.label("usage")
        fb.cbr(ok, parse, usage)
        fb.block(usage)
        argv0 = fb.load("ptr", "%argv")
        fb.emit(f"call void @ax_usage(ptr {argv0.name}, ptr {self.string(' '.join(p.name for p in m.params))})")
        fb.emit("ret i32 2")
        fb.block(parse)
        env: Dict[str, Slot] = {}
        for i, p in enumerate(m.params):
            ap = fb.ins(f"getelementptr inbounds ptr, ptr %argv, i64 {i + 1}", "ptr")
            s = fb.load("ptr", ap.name)
            v = fb.ins(f"call i64 @ax_parse_i64(ptr {s.name}, ptr {self.string(p.name)})", "i64")
            ptr = fb.alloca("i64"); fb.store(v, ptr); env[p.name] = Slot(ptr, "i64", axty=S.I64)
        for p in m.params:
            for q in p.preds:
                c, _ = self.gen(q, env, par=False)
                good, bad = fb.label("pre_ok"), fb.label("pre_fail")
                fb.cbr(c, good, bad)
                fb.block(bad)
                fb.emit(f"call void @ax_precondition_failed(ptr {self.string(S.show(q))})")
                fb.emit("ret i32 3")
                fb.block(good)
        args = ", ".join(str(fb.load("i64", env[p.name].ptr)) for p in m.params)
        if m.ret == S.UNIT:
            fb.emit(f"call void {self.fname(m,'par')}({args})")
            r = None
        else: r = fb.ins(f"call {lty(m.ret)} {self.fname(m, 'par')}({args})", lty(m.ret))
        if isinstance(m.ret, S.TArr):                      # main returned bytes: write them to stdout
            data = fb.ins(f"extractvalue {r}, 0", "ptr"); ln = fb.ins(f"extractvalue {r}, 1", "i64")
            fb.emit(f"call void @ax_print_bytes(ptr {data.name}, i64 {ln.name})")
            self.maybe_free(r, self.nz(fb.ins(f"extractvalue {r}, 2", "i64")), m.ret)
        elif m.ret != S.UNIT:
            fb.emit(f"call void @ax_print_{'f64' if m.ret == S.F64 else 'i64'}({r})")
        fb.emit("ret i32 0")
        self.funcs.append(fb.render())
        if "linux" in self.triple:
            # The kernel enters with argc/argv/envp on the initial stack. This
            # is machine entry glue; no CRT or libc startup object is linked.
            if self.triple.startswith(("aarch64", "arm64")):
                entry = 'mov x0, sp; bl ax_process_start; brk #0'
            else:
                entry = 'movq %rsp, %rdi; andq $$-16, %rsp; callq ax_process_start; ud2'
            entry = entry.replace('; ', '\\0A')
            self.funcs.append(f'define void @_start() naked noreturn {{\n  call void asm sideeffect "{entry}", "~{{memory}}"()\n  unreachable\n}}')
            self.funcs.append('''define void @ax_process_start(ptr %stack) noreturn {
  %argc64 = load i64, ptr %stack
  %argc = trunc i64 %argc64 to i32
  %argv = getelementptr i64, ptr %stack, i64 1
  %status = call i32 @main(i32 %argc, ptr %argv)
  %status64 = sext i32 %status to i64
  call void @ax_exit(i64 %status64)
  unreachable
}''')

    # ------------------------------------------------------------ helpers
    def nz(self, own: Val) -> Optional[Val]:
        """i64 ownership flag -> i1 condition; None means statically false."""
        if own.name == "0": return None
        if own.name == "1": return Val("true", "i1")
        return self.fb.ins(f"icmp ne i64 {own.name}, 0", "i1")

    def and_(self, a: Optional[Val], b: Optional[Val]) -> Optional[Val]:
        if a is None or b is None: return None
        if a.name == "true": return b
        if b.name == "true": return a
        return self.fb.ins(f"and i1 {a.name}, {b.name}", "i1")

    def capacity(self, length: Val) -> Val:
        """The runtime's capacity class for a length: max(16, next power of two), inline."""
        fb = self.fb
        m1 = fb.ins(f"sub i64 {length.name}, 1", "i64")
        lz = fb.ins(f"call i64 @llvm.ctlz.i64(i64 {m1.name}, i1 false)", "i64")
        sh = fb.ins(f"sub i64 64, {lz.name}", "i64")
        p2 = fb.ins(f"shl i64 1, {sh.name}", "i64")
        small = fb.ins(f"icmp sle i64 {length.name}, 16", "i1")
        return fb.ins(f"select i1 {small.name}, i64 16, i64 {p2.name}", "i64")

    def with_own(self, arr: Val, own: Val) -> Val:
        return self.fb.ins(f"insertvalue {arr}, i64 {own.name}, 2", ARR)

    def maybe_free(self, arr: Val, cond: Optional[Val], t: S.TArr):
        """Free arr's buffer if cond (i1) holds; cond None = never, 'true' = always."""
        fb = self.fb
        if cond is None: return
        if cond.name != "true":
            do, cont = fb.label("free"), fb.label("cont")
            fb.cbr(cond, do, cont); fb.block(do)
        if managed(t.elem):
            length = fb.ins(f'extractvalue {arr}, 1', 'i64')
            self.counted_loop(Val('0', 'i64'), length, '$drop', {},
                              lambda env, i: self.drop_value(self.load_elem(arr, i, t), t.elem))
        data = fb.ins(f"extractvalue {arr}, 0", "ptr")
        ln = fb.ins(f"extractvalue {arr}, 1", "i64")
        by = fb.ins(f"mul i64 {ln.name}, {elem_size(t)}", "i64")
        fb.emit(f"call void @ax_free(ptr {data.name}, i64 {by.name})")
        if cond.name != "true":
            fb.br(cont); fb.block(cont)

    def drop_value(self, value, ty):
        if isinstance(ty, S.TArr):
            own = self.fb.ins(f'extractvalue {value}, 2', 'i64')
            self.maybe_free(value, self.nz(own), ty)
        elif isinstance(ty, S.TRec):
            for k, (_, field) in enumerate(ty.fields):
                if managed(field):
                    child = self.fb.ins(f'extractvalue {value}, {k}', lty(field))
                    self.drop_value(child, field)

    def drop_record_slot(self, slot):
        fb = self.fb
        if not slot.own_ptr: return  # borrowed region capture
        flag = self.nz(fb.load('i64', slot.own_ptr))
        do, end = fb.label('record_drop'), fb.label('record_end')
        fb.cbr(flag, do, end); fb.block(do)
        self.drop_value(fb.load(slot.ty, slot.ptr), slot.axty)
        fb.br(end); fb.block(end)

    def clone_value(self, value, ty):
        fb = self.fb
        if isinstance(ty, S.TArr):
            length = fb.ins(f'extractvalue {value}, 1', 'i64')
            # Empty values own no storage. Avoid allocator traffic when copying
            # result/option records containing empty strings or arrays.
            result = fb.alloca(ARR)
            empty, nonempty, end = fb.label('clone_empty'), fb.label('clone_data'), fb.label('clone_end')
            fb.cbr(fb.ins(f'icmp eq i64 {length.name}, 0', 'i1'), empty, nonempty)
            fb.block(empty); fb.store(Val('zeroinitializer', ARR), result); fb.br(end)
            fb.block(nonempty)
            out = self.new_array(ty, length)
            if managed(ty.elem):
                self.counted_loop(Val('0', 'i64'), length, '$clone', {},
                    lambda env, i: self.store_elem(out, i, ty, self.clone_value(self.load_elem(value, i, ty), ty.elem)))
            else:
                src = fb.ins(f'extractvalue {value}, 0', 'ptr'); dst = fb.ins(f'extractvalue {out}, 0', 'ptr')
                size = fb.ins(f'mul i64 {length.name}, {elem_size(ty)}', 'i64')
                fb.emit(f'call void @llvm.memcpy.p0.p0.i64(ptr {dst.name}, ptr {src.name}, i64 {size.name}, i1 false)')
            fb.store(out, result); fb.br(end); fb.block(end)
            return fb.load(ARR, result)
        if isinstance(ty, S.TRec):
            out = value
            for k, (_, field) in enumerate(ty.fields):
                if managed(field):
                    child = fb.ins(f'extractvalue {value}, {k}', lty(field))
                    out = fb.ins(f'insertvalue {out}, {self.clone_value(child, field)}, {k}', lty(ty))
            return out
        return value

    def owning_value(self, value, own, ty):
        if not isinstance(ty, S.TArr): return value
        if own.name == '1': return self.with_own(value, own)
        if own.name == '0': return self.clone_value(value, ty)
        fb = self.fb; result = fb.alloca(ARR)
        take, copy, end = fb.label('take'), fb.label('clone'), fb.label('owned')
        fb.cbr(self.nz(own), take, copy)
        fb.block(take); fb.store(self.with_own(value, own), result); fb.br(end)
        fb.block(copy); fb.store(self.clone_value(value, ty), result); fb.br(end)
        fb.block(end); return fb.load(ARR, result)

    def free_scope(self, slots: List[Slot], result_own: Optional[Val]):
        """At scope exit free every array slot that still holds its buffer. If the scope's
        result is an array that might be a borrowed alias (own == 0), keep the buffers alive."""
        fb = self.fb
        guard = Val("true", "i1") if result_own is None else self.nz(result_own)
        for s in slots:
            if isinstance(s.axty, S.TRec) and managed(s.axty):
                self.drop_record_slot(s)
                continue
            if not s.own_ptr: continue
            flag = self.nz(fb.load("i64", s.own_ptr))
            self.maybe_free(fb.load(ARR, s.ptr), self.and_(flag, guard), s.axty)

    def new_array(self, t: S.TArr, length: Val) -> Val:
        fb = self.fb
        neg = fb.ins(f"icmp slt i64 {length.name}, 0", "i1")
        ln = fb.ins(f"select i1 {neg.name}, i64 0, i64 {length.name}", "i64")
        maximum=(1<<62)//max(1,elem_size(t))
        large=fb.ins(f'icmp ugt i64 {ln.name}, {maximum}','i1')
        self.trap_if(large,self.fn.body,'array allocation size overflow')
        by = fb.ins(f"mul i64 {ln.name}, {elem_size(t)}", "i64")
        result = fb.alloca(ARR)
        empty, allocate, end = fb.label('array_empty'), fb.label('array_allocate'), fb.label('array_ready')
        fb.cbr(fb.ins(f'icmp eq i64 {ln.name}, 0','i1'), empty, allocate)
        fb.block(empty); fb.store(Val('zeroinitializer',ARR),result); fb.br(end)
        fb.block(allocate)
        data = fb.ins(f"call ptr @ax_alloc(i64 {by.name})", "ptr")
        a = fb.ins(f"insertvalue {ARR} undef, ptr {data.name}, 0", ARR)
        a = fb.ins(f"insertvalue {a}, i64 {ln.name}, 1", ARR)
        a = fb.ins(f"insertvalue {a}, i64 1, 2", ARR)
        fb.store(a,result); fb.br(end); fb.block(end)
        a=fb.load(ARR,result); self.assume_array(a, t)
        return a

    def assume_array(self, a: Val, t: S.TArr):
        if not self.assume: return
        fb = self.fb
        data = fb.ins(f"extractvalue {a}, 0", "ptr")
        ln = fb.ins(f"extractvalue {a}, 1", "i64")
        by = fb.ins(f"mul i64 {ln.name}, {elem_size(t)}", "i64")
        nn = fb.ins(f"icmp sge i64 {ln.name}, 0", "i1")
        fb.emit(f"call void @llvm.assume(i1 {nn.name})")
        fb.emit(f'call void @llvm.assume(i1 true) ["dereferenceable"(ptr {data.name}, i64 {by.name})]')

    def assume_index(self, idx: Val, ln: Val):
        if not self.assume: return
        fb = self.fb
        a = fb.ins(f"icmp sge i64 {idx.name}, 0", "i1"); fb.emit(f"call void @llvm.assume(i1 {a.name})")
        b = fb.ins(f"icmp slt i64 {idx.name}, {ln.name}", "i1"); fb.emit(f"call void @llvm.assume(i1 {b.name})")

    # -- element addressing (scalar arrays: one plane; record arrays: struct-of-arrays in one buffer)
    def elem_ptrs(self, arr: Val, idx: Val, t: S.TArr) -> List[Tuple[int, str, Val]]:
        """[(field index or -1, llvm type, pointer)] for the element at idx."""
        fb = self.fb
        data = fb.ins(f"extractvalue {arr}, 0", "ptr")
        ln = fb.ins(f"extractvalue {arr}, 1", "i64")
        self.assume_index(idx, ln)
        if not isinstance(t.elem, S.TRec):
            return [(-1, lty(t.elem), fb.ins(f"getelementptr inbounds {lty(t.elem)}, ptr {data.name}, i64 {idx.name}", "ptr"))]
        out = []
        for k, fty, size, prefix in rec_layout(t.elem):
            if prefix:
                off = fb.ins(f"mul i64 {ln.name}, {prefix}", "i64")
                base = fb.ins(f"getelementptr inbounds i8, ptr {data.name}, i64 {off.name}", "ptr")
            else:
                base = data
            out.append((k, fty, fb.ins(f"getelementptr inbounds {fty}, ptr {base.name}, i64 {idx.name}", "ptr")))
        return out

    def load_elem(self, arr: Val, idx: Val, t: S.TArr, only_field: Optional[int] = None) -> Val:
        fb = self.fb
        ptrs = self.elem_ptrs(arr, idx, t)
        if not isinstance(t.elem, S.TRec): return fb.load(ptrs[0][1], ptrs[0][2].name)
        if only_field is not None:
            k, fty, p = next(x for x in ptrs if x[0] == only_field)
            return fb.load(fty, p.name)
        rec = Val("undef", lty(t.elem))
        for k, fty, p in ptrs:
            v = fb.load(fty, p.name)
            rec = fb.ins(f"insertvalue {rec}, {v}, {k}", lty(t.elem))
        return rec

    def store_elem(self, arr: Val, idx: Val, t: S.TArr, v: Val):
        fb = self.fb
        ptrs = self.elem_ptrs(arr, idx, t)
        if not isinstance(t.elem, S.TRec): fb.store(v, ptrs[0][2].name); return
        for k, fty, p in ptrs:
            fv = fb.ins(f"extractvalue {v}, {k}", fty)
            fb.store(fv, p.name)

    def add_vals(self, a: Val, b: Val, t) -> Val:
        fb = self.fb
        if t == S.F64: return fb.ins(f"fadd reassoc {a}, {b.name}", "double")   # Axon sums are order-free
        if t == S.I64:
            return self.checked_operation('+',a,b,self.fn.body) if self.checked_integer() else fb.ins(f"add {a}, {b.name}", "i64")
        acc = a
        for k, (_, ft) in enumerate(t.fields):
            x = fb.ins(f"extractvalue {acc}, {k}", lty(ft)); y = fb.ins(f"extractvalue {b}, {k}", lty(ft))
            acc = fb.ins(f"insertvalue {acc}, {self.add_vals(x, y, ft)}, {k}", lty(t))
        return acc

    # ------------------------------------------------------------ expressions
    def bounded_remainder(self, e, a, b, lanes=1):
        """Strength-reduce small, statically bounded signed remainders."""
        bounds = self.value_ranges.get(id(e.left))
        if not bounds or not isinstance(e.right, S.Num) or e.right.value <= 0: return None
        d = e.right.value
        lo, hi = bounds
        limit = max(abs(lo), abs(hi))
        if lo == Ranges.MIN or limit >= d * 8 or d * 8 > Ranges.MAX: return None
        fb = self.fb
        negative = None
        value = a
        width = 32 if -(1 << 31) <= lo <= hi < (1 << 31) and d * 8 < (1 << 31) else 64
        scalar = f"i{width}"
        ty = scalar if lanes == 1 else f'<{lanes} x {scalar}>'
        boolty = 'i1' if lanes == 1 else f'<{lanes} x i1>'
        wide = 'i64' if lanes == 1 else f'<{lanes} x i64>'
        def constant(n): return str(n) if lanes == 1 else '<'+', '.join(f'{scalar} {n}' for _ in range(lanes))+'>'
        if width == 32: value = fb.ins(f"trunc {a} to {ty}", ty)
        if lo < 0:
            negative = fb.ins(f"icmp slt {value}, {constant(0)}", boolty)
            negated = fb.ins(f"sub {ty} {constant(0)}, {value.name}", ty)
            value = fb.ins(f"select {negative}, {negated}, {value}", ty)
        for factor in (4, 2, 1):
            threshold = d * factor
            if limit < threshold: continue
            ge = fb.ins(f"icmp uge {value}, {constant(threshold)}", boolty)
            reduced = fb.ins(f"sub {value}, {constant(threshold)}", ty)
            value = fb.ins(f"select {ge}, {reduced}, {value}", ty)
        if negative is not None:
            negated = fb.ins(f"sub {ty} {constant(0)}, {value.name}", ty)
            value = fb.ins(f"select {negative}, {negated}, {value}", ty)
        if width == 32: value = fb.ins(f"sext {value} to {wide}", wide)
        self.bounded_remainders.append({'fn': self.fn.name, 'pos': list(e.pos), 'divisor': d, 'bounds': list(bounds), 'width': width, 'lanes':lanes})
        return value

    def checked_integer(self):
        from pathlib import Path
        source=getattr(self.fn,'source',None)
        return not source or not Path(source).resolve().is_relative_to(Path(__file__).resolve().parents[1]/'runtime')

    def trap_if(self, condition, node, message):
        fb=self.fb;bad,good=fb.label('arithmetic_bad'),fb.label('arithmetic_ok')
        fb.cbr(condition,bad,good);fb.block(bad)
        fb.emit(f'call void @ax_trap(ptr {self.string(message+" at "+self.fn.name+":"+str(node.pos[0]))})')
        fb.emit('unreachable');fb.block(good)
        self.overflow_checks.append({'fn':self.fn.name,'pos':list(node.pos),'kind':message})

    def checked_operation(self, op, a, b, node, proven=False):
        operation={'+':'add','-':'sub','*':'mul'}[op]
        if proven:return self.fb.ins(f'{operation} {a}, {b.name}','i64')
        def bounds(value, expression):
            try:v=int(value.name);return v,v
            except ValueError:return self.value_ranges.get(id(expression),Ranges.TOP)
        left=bounds(a,getattr(node,'left',None))
        right=bounds(b,getattr(node,'right',getattr(node,'expr',None)))
        if left is None:left=Ranges.TOP
        if right is None:right=Ranges.TOP
        if op=='+':lo,hi=left[0]+right[0],left[1]+right[1]
        elif op=='-':lo,hi=left[0]-right[1],left[1]-right[0]
        else:
            possibilities=[x*y for x in left for y in right];lo,hi=min(possibilities),max(possibilities)
        if Ranges.MIN<=lo<=hi<=Ranges.MAX:return self.fb.ins(f'{operation} {a}, {b.name}','i64')
        result=self.fb.ins(f'call {{ i64, i1 }} @llvm.s{operation}.with.overflow.i64({a}, {b})','{ i64, i1 }')
        value=self.fb.ins(f'extractvalue {result}, 0','i64')
        overflow=self.fb.ins(f'extractvalue {result}, 1','i1')
        self.trap_if(overflow,node,'integer overflow')
        return value

    def gen(self, e: S.Node, env: Dict[str, Slot], par: bool) -> Tuple[Val, Optional[Val]]:
        fb = self.fb
        if isinstance(e, S.Num): return Val(str(e.value), "i64"), None
        if isinstance(e, S.Flt): return Val(fconst(e.value), "double"), None
        if isinstance(e, S.BoolLit): return Val("true" if e.value else "false", "i1"), None
        if isinstance(e, S.Byte): return Val(str(e.value), "i8"), None
        if isinstance(e, S.Str):
            g = self.bytes_const(e.value)
            a = fb.ins(f"insertvalue {ARR} undef, ptr {g}, 0", ARR)
            a = fb.ins(f"insertvalue {a}, i64 {len(e.value)}, 1", ARR)
            a = fb.ins(f"insertvalue {a}, i64 0, 2", ARR)           # static data: never owned
            return a, Val("0", "i64")
        if isinstance(e, S.Var):
            s = env[e.name]
            v = fb.load(s.ty, s.ptr)
            if getattr(e, 'resource_move', False) and s.resource_live:
                fb.store(Val('false', 'i1'), s.resource_live)
            if s.ty != ARR:
                if managed(s.axty) and s.own_ptr and id(e) in s.consume:
                    fb.store(Val('0','i64'), s.own_ptr); return v, None
                return self.clone_value(v, s.axty) if managed(s.axty) else v, None
            if s.own_ptr and id(e) in s.consume:                    # move: take the flag, leave 0 behind
                own = fb.load("i64", s.own_ptr); fb.store(Val("0", "i64"), s.own_ptr)
                return v, own
            return v, Val("0", "i64")
        if isinstance(e, S.Bin):
            if e.op in ("&&", "||"):
                a, _ = self.gen(e.left, env, par)
                res = fb.alloca("i1"); fb.store(a, res)
                rhs, join = fb.label("rhs"), fb.label("join")
                if e.op == "&&": fb.cbr(a, rhs, join)
                else: fb.cbr(a, join, rhs)
                fb.block(rhs)
                b, _ = self.gen(e.right, env, par); fb.store(b, res); fb.br(join)
                fb.block(join)
                return fb.load("i1", res), None
            a, _ = self.gen(e.left, env, par)
            b, _ = self.gen(e.right, env, par)
            fl = a.ty == "double"
            if e.op == "%" and a.ty == "i64":
                reduced = self.bounded_remainder(e, a, b)
                if reduced is not None: return reduced, None
            if e.op in ("+", "-", "*", "/", "%"):
                if not fl and a.ty=='i64' and self.checked_integer():
                    if e.op in ('+','-','*'):
                        return self.checked_operation(e.op,a,b,e,getattr(e,'arithmetic_proven',False)),None
                    overflow = fb.ins(f'icmp eq i64 {a.name}, -9223372036854775808','i1')
                    negative = fb.ins(f'icmp eq i64 {b.name}, -1','i1')
                    bad = fb.ins(f'and i1 {overflow.name}, {negative.name}','i1')
                    self.trap_if(bad,e,'integer division overflow')
                op = {"+": "fadd" if fl else "add", "-": "fsub" if fl else "sub", "*": "fmul" if fl else "mul",
                      "/": "fdiv" if fl else "sdiv", "%": "srem"}[e.op]
                return fb.ins(f"{op} {a}, {b.name}", a.ty), None
            if fl:
                op = {"<": "olt", "<=": "ole", ">": "ogt", ">=": "oge", "==": "oeq", "!=": "une"}[e.op]
                return fb.ins(f"fcmp {op} {a}, {b.name}", "i1"), None
            if a.ty == "i8":
                op = {"<": "ult", "<=": "ule", ">": "ugt", ">=": "uge", "==": "eq", "!=": "ne"}[e.op]
                return fb.ins(f"icmp {op} {a}, {b.name}", "i1"), None
            op = {"<": "slt", "<=": "sle", ">": "sgt", ">=": "sge", "==": "eq", "!=": "ne"}[e.op]
            return fb.ins(f"icmp {op} {a}, {b.name}", "i1"), None
        if isinstance(e, S.Un):
            if e.op=='-' and isinstance(e.expr,S.Num) and e.expr.value==1<<63:return Val('-9223372036854775808','i64'),None
            a, _ = self.gen(e.expr, env, par)
            if e.op == "!": return fb.ins(f"xor i1 {a.name}, true", "i1"), None
            if a.ty == "double": return fb.ins(f"fneg {a}", "double"), None
            if self.checked_integer():return self.checked_operation('-',Val('0','i64'),a,e,getattr(e,'arithmetic_proven',False)),None
            return fb.ins(f"sub i64 0, {a.name}", "i64"), None
        if isinstance(e, S.Field) and isinstance(e.expr, S.Index):
            a=self.record_argument_view(e.expr.arr,env)
            if a is None:a,own=self.gen(e.expr.arr,env,par)
            else:own=Val('0','i64')
            i, _ = self.gen(e.expr.idx, env, par)
            v = self.load_elem(a, i, e.expr.arr.ty, only_field=e.expr.arr.ty.elem.index(e.name))
            if managed(e.ty):v=self.clone_value(v,e.ty)
            self.maybe_free(a, self.nz(own), e.expr.arr.ty)
            return v, fb.ins(f'extractvalue {v}, 2','i64') if isinstance(e.ty,S.TArr) else None
        if isinstance(e, S.Index):
            a=self.record_argument_view(e.arr,env)
            if a is None:a,own=self.gen(e.arr,env,par)
            else:own=Val('0','i64')
            i, _ = self.gen(e.idx, env, par)
            v = self.load_elem(a, i, e.arr.ty)
            if managed(e.ty): v = self.clone_value(v, e.ty)
            self.maybe_free(a, self.nz(own), e.arr.ty)
            return v, fb.ins(f'extractvalue {v}, 2', 'i64') if isinstance(e.ty, S.TArr) else None
        if isinstance(e, S.Len):
            borrowed=self.record_argument_view(e.expr,env)
            if borrowed is not None:return fb.ins(f'extractvalue {borrowed}, 1','i64'),None
            a, own = self.gen(e.expr, env, par)
            n = fb.ins(f"extractvalue {a}, 1", "i64")
            self.maybe_free(a, self.nz(own), e.expr.ty)
            return n, None
        if isinstance(e, S.Cast):
            a, _ = self.gen(e.expr, env, par)
            if e.to == S.I32: return fb.ins(f"trunc {a} to i32", "i32"), None
            if a.ty == "i32": return fb.ins(f"sext {a} to i64", "i64"), None
            if e.to == S.F64: return fb.ins(f"sitofp {a} to double", "double"), None
            if e.to == S.U8: return fb.ins(f"trunc {a} to i8", "i8"), None
            if a.ty == "i8": return fb.ins(f"zext {a} to i64", "i64"), None
            low=fb.ins(f'fcmp oge double {a.name}, {fconst(float(-(1<<63)))}','i1')
            high=fb.ins(f'fcmp olt double {a.name}, {fconst(float(1<<63))}','i1')
            valid=fb.ins(f'and i1 {low.name}, {high.name}','i1')
            bad=fb.ins(f'xor i1 {valid.name}, true','i1');self.trap_if(bad,e,'float to integer out of range')
            return fb.ins(f"fptosi {a} to i64", "i64"), None
        if isinstance(e, S.RecLit):
            rt = e.ty; given = dict(e.fields)
            rec = Val("undef", lty(rt))
            for k, (fname, ft) in enumerate(rt.fields):
                v, own = self.gen(given[fname], env, par)
                if isinstance(ft, S.TArr) and not isinstance(given[fname], S.Str): v = self.owning_value(v, own, ft)
                rec = fb.ins(f"insertvalue {rec}, {v}, {k}", lty(rt))
            return rec, None
        if isinstance(e, S.Field):
            borrowed = isinstance(e.expr, S.Var)
            r = fb.load(env[e.expr.name].ty, env[e.expr.name].ptr) if borrowed else self.gen(e.expr, env, par)[0]
            if borrowed:
                slot = env[e.expr.name]
                if slot.own_ptr and id(e.expr) in slot.consume:
                    fb.store(Val('0','i64'), slot.own_ptr); borrowed = False
            k = e.expr.ty.index(e.name)
            value = fb.ins(f"extractvalue {r}, {k}", lty(e.ty))
            if borrowed and slot.own_ptr and id(e) in slot.field_consume.get(e.name,set()):
                # Transfer just this field; scope cleanup still owns the other fields.
                rest = fb.ins(f'insertvalue {r}, {lty(e.ty)} {zero(e.ty)}, {k}',lty(e.expr.ty))
                fb.store(rest,slot.ptr)
                return value, fb.ins(f'extractvalue {value}, 2','i64') if isinstance(e.ty,S.TArr) else None
            if borrowed and isinstance(e.ty, S.TArr) and slot.borrowed:
                return self.with_own(value, Val('0','i64')), Val('0','i64')
            if borrowed and managed(e.ty): value = self.clone_value(value, e.ty)
            if not borrowed:
                for j, (_, field) in enumerate(e.expr.ty.fields):
                    if j != k and managed(field):
                        self.drop_value(fb.ins(f'extractvalue {r}, {j}',lty(field)),field)
            return value, fb.ins(f'extractvalue {value}, 2', 'i64') if isinstance(e.ty, S.TArr) else None
        if isinstance(e, S.With):
            r, _ = self.gen(e.expr, env, par)
            rt = e.expr.ty
            for fname, fe in e.fields:
                v, own = self.gen(fe, env, par)
                if managed(fe.ty):
                    old = fb.ins(f'extractvalue {r}, {rt.index(fname)}', lty(fe.ty))
                    self.drop_value(old, fe.ty)
                    if isinstance(fe.ty, S.TArr): v = self.owning_value(v, own, fe.ty)
                r = fb.ins(f"insertvalue {r}, {v}, {rt.index(fname)}", lty(rt))
            return r, None
        if isinstance(e, S.Call):
            return self.call(e, env, par)
        if isinstance(e, S.Let):
            v, own = self.gen(e.value, env, par)
            ptr = fb.alloca(v.ty); fb.store(v, ptr)
            slot = Slot(ptr, v.ty, axty=e.value.ty)
            if getattr(e, 'resource_scope', False):
                slot.resource_live = fb.alloca("i1"); fb.store(Val("true", "i1"), slot.resource_live)
            if v.ty == ARR:
                slot.own_ptr = fb.alloca("i64"); fb.store(own, slot.own_ptr)
                slot.consume = consume_plan([e.body], e.name)
                self.assume_array(v, e.value.ty)
            elif managed(e.value.ty):
                slot.own_ptr = fb.alloca('i64'); fb.store(Val('1','i64'),slot.own_ptr)
                slot.consume = consume_plan([e.body],e.name)
                slot.field_consume = field_consume_plan([e.body],e.name,e.value.ty)
            env2 = dict(env); env2[e.name] = slot
            bv, bown = self.gen(e.body, env2, par)
            if managed(e.value.ty): self.free_scope([slot], bown if isinstance(e.body.ty, S.TArr) else None)
            return bv, bown
        if isinstance(e, S.If):
            c, _ = self.gen(e.cond, env, par)
            ty = lty(e.ty); res = fb.alloca(ty)
            ores = fb.alloca("i64") if ty == ARR else None
            then, els, join = fb.label("then"), fb.label("else"), fb.label("join")
            fb.cbr(c, then, els)
            for label, branch in ((then, e.then), (els, e.els)):
                fb.block(label)
                v, own = self.gen(branch, env, par)
                fb.store(v, res)
                if ores: fb.store(own, ores)
                fb.br(join)
            fb.block(join)
            return fb.load(ty, res), (fb.load("i64", ores) if ores else None)
        if isinstance(e, S.Check):
            p, _ = self.gen(e.pred, env, par)
            ok, bad = fb.label("chk_ok"), fb.label("chk_fail")
            fb.cbr(p, ok, bad); fb.block(bad)
            fb.emit(f"call void @ax_trap(ptr {self.string(f'check failed: {S.show(e.pred)} at {e.pos[0]}:{e.pos[1]}')})")
            fb.emit("unreachable"); fb.block(ok)
            return self.gen(e.body, env, par)
        if isinstance(e, (S.Sum, S.ArrC)):
            return self.comprehension(e, env, par)
        if isinstance(e, S.Loop):
            return self.loop(e, env, par)
        raise TypeError(type(e))

    # ------------------------------------------------------------ calls and builtins
    def record_argument_view(self, e, env):
        """Borrow only storage rooted in a live lexical binding, never a temporary."""
        if isinstance(e, S.Var):
            slot = env[e.name]
            return self.fb.load(slot.ty, slot.ptr)
        if isinstance(e, S.Field):
            parent = self.record_argument_view(e.expr, env)
            if parent is not None:
                return self.fb.ins(f'extractvalue {parent}, {e.expr.ty.index(e.name)}', lty(e.ty))
        return None

    def call(self, e: S.Call, env, par, resource_guard=True):
        fb = self.fb
        if resource_guard and getattr(e, 'resource_cleanup', False):
            slot = env[e.args[0].name]
            if slot.resource_live:
                live = fb.load('i1', slot.resource_live)
                cleanup, join = fb.label('resource_drop'), fb.label('resource_join')
                fb.cbr(live, cleanup, join); fb.block(cleanup)
                fb.store(Val('false', 'i1'), slot.resource_live)
                self.call(e, env, par, resource_guard=False)
                fb.br(join); fb.block(join)
                return Val('0', 'i8'), None
        b = getattr(e, "builtin", None)
        if b=='ref_null':return Val('zeroinitializer',lty(e.ty)),None
        if b=='adopt':
            resource=e.resource_target;value,_=self.gen(e.args[0],env,par);arena,_=self.gen(e.args[1],env,par)
            token=fb.ins(f'extractvalue {arena}, 0','i64')
            finalizer=self.resource_finalizers.get(resource.name)
            if finalizer is None:
                finalizer='@ax_resource_finalizer_'+str(len(self.resource_finalizers));self.resource_finalizers[resource.name]=finalizer
                saved=self.fb;dropper=FuncBuilder(f'define internal void {finalizer}(ptr %box)');self.fb=dropper
                payload=dropper.load(lty(resource),'%box');dropper.emit(f'call void {self.fname(e.resource_drop,"seq")}({payload})');dropper.emit('ret void');self.funcs.append(dropper.render());self.fb=saved
            box=fb.ins(f'call ptr @ax_alloc(i64 {scalar_size(resource)})','ptr');fb.store(value,box.name)
            slot=fb.ins(f'call i64 @ax_rt_arena_register__seq({token}, {box}, ptr {finalizer})','i64')
            failed=fb.ins(f'icmp slt i64 {slot.name}, 0','i1')
            self.trap_if(failed,e,'resource arena is closed or full')
            ref=fb.ins(f'insertvalue {lty(e.ty)} zeroinitializer, {token}, 0',lty(e.ty));return fb.ins(f'insertvalue {ref}, {slot}, 1',lty(e.ty)),None
        if b in ('borrow_ref','ref_live','ref_release'):
            reference,_=self.gen(e.args[0],env,par);token=fb.ins(f'extractvalue {reference}, 0','i64');slot=fb.ins(f'extractvalue {reference}, 1','i64')
            if b=='ref_release':fb.emit(f'call void @ax_rt_arena_release__seq({token}, {slot})');return Val('0','i8'),None
            pointer=fb.ins(f'call ptr @ax_rt_arena_lookup__seq({token}, {slot})','ptr');live=fb.ins(f'icmp ne ptr {pointer.name}, null','i1')
            if b=='ref_live':return live,None
            self.trap_if(fb.ins(f'xor i1 {live.name}, true','i1'),e,'borrow of a closed resource reference')
            value=fb.load(lty(e.ty),pointer.name);return self.clone_value(value,e.ty) if managed(e.ty) else value,None
        if b=='function_null':return Val('null','ptr'),None
        if b=='function':return Val(self.fname(e.target,'seq'),'ptr'),None
        if b=='allocation_stats_enabled':return Val('true' if os.environ.get('AXON_ALLOC_STATS')=='1' else 'false','i1'),None
        if b in ('bit_and','bit_or','bit_xor'):
            a,c=[self.gen(x,env,par)[0] for x in e.args]
            return fb.ins(f'{b[4:]} {a}, {c.name}','i64'),None
        if b == 'byte_swap64':
            a=self.gen(e.args[0],env,par)[0]
            return fb.ins(f'call i64 @llvm.bswap.i64({a})','i64'),None
        if b in ('shift_left','shift_right','arithmetic_shift_right'):
            a,c=[self.gen(x,env,par)[0] for x in e.args]
            count=fb.ins(f'and i64 {c.name}, 63','i64')
            return fb.ins(f'{dict(shift_left="shl",shift_right="lshr",arithmetic_shift_right="ashr")[b]} {a}, {count.name}','i64'),None
        if b in ('wrapping_add','wrapping_sub','wrapping_mul'):
            a,c=[self.gen(x,env,par)[0] for x in e.args]
            return fb.ins(f'{b[9:]} {a}, {c.name}','i64'),None
        if b == "null": return Val("null", "ptr"), None
        if b == "noop": return Val("0", "i8"), None
        if b == "runtime_state": return Val("@ax_runtime_state", "ptr"), None
        if b in ('atomic_load_i64','atomic_store_i64','atomic_compare_exchange_i64'):
            args=[self.gen(a,env,False)[0] for a in e.args]
            p=fb.ins(f'getelementptr i8, {args[0]}, {args[1]}','ptr')
            if b=='atomic_load_i64':return fb.ins(f'load atomic i64, {p} acquire, align 8','i64'),None
            if b=='atomic_store_i64':
                fb.emit(f'store atomic {args[2]}, {p} release, align 8');return Val('0','i8'),None
            value=fb.ins(f'cmpxchg {p}, {args[2]}, {args[3]} acq_rel acquire, align 8','{ i64, i1 }')
            return fb.ins(f'extractvalue {value}, 0','i64'),None
        if b in ('counter_ticks','counter_frequency'):
            if not self.triple.startswith(('arm64','aarch64')):raise ValueError(b+' requires an ARM64 target')
            instruction='isb\\0Amrs $0, cntvct_el0' if b=='counter_ticks' else 'mrs $0, cntfrq_el0'
            return fb.ins(f'call i64 asm sideeffect "{instruction}", "=r,~{{memory}}"()','i64'),None
        if b == 'spawn_region':
            args = [self.gen(a,env,False)[0] for a in e.args]
            return fb.ins('call i64 @ax_spawn_region('+', '.join(map(str,args))+')','i64'),None
        if b == "stack_alloc":
            size, _ = self.gen(e.args[0], env, False)
            return fb.ins(f"alloca i8, {size}, align 16", "ptr"), None
        if b in ("ptr_to_i64", "ptr_from_i64", "float_bits", "bits_float"):
            value, _ = self.gen(e.args[0], env, False)
            operation = {"ptr_to_i64": "ptrtoint", "ptr_from_i64": "inttoptr", "float_bits": "bitcast", "bits_float": "bitcast"}[b]
            return fb.ins(f"{operation} {value} to {lty(e.ty)}", lty(e.ty)), None
        if b in ("syscall6", "fork_syscall"):
            darwin = "apple" in self.triple
            arm = self.triple.startswith(("aarch64", "arm64"))
            if b == "fork_syscall":
                if not darwin:
                    number = 220 if arm else 57
                    vals = [Val(str(number), "i64")] + [Val("0", "i64")] * 6
                    if arm: vals[1] = Val("17", "i64")  # clone(SIGCHLD), fork semantics
                else: vals = [Val("2", "i64")] + [Val("0", "i64")] * 6
            else: vals = [self.gen(a, env, False)[0] for a in e.args]
            number, *args = vals
            if arm:
                asm = "svc #0x80; cneg x0, x0, cs" if darwin else "svc #0"
                if b == "fork_syscall" and darwin:
                    asm = "svc #0x80; b.cs 1f; cbz x1, 2f; mov x0, #0; b 2f; 1: neg x0, x0; 2:"
                # Darwin returns a second value in x1 (fork's child flag,
                # getpid's parent PID, etc.). Mark it as an output so LLVM
                # cannot reuse the pre-syscall x1 value after the trap.
                constraints = "={x0},={x1},0,1,{x2},{x3},{x4},{x5},{x16},~{memory},~{cc}" if darwin else "={x0},0,{x1},{x2},{x3},{x4},{x5},{x8},~{memory},~{cc}"
            else:
                asm = "syscall; jnc 1f; negq %rax; 1:" if darwin else "syscall"
                if darwin: number = fb.ins(f"add {number}, 33554432", "i64")
                constraints = "={rax},{rdi},{rsi},{rdx},{r10},{r8},{r9},{rax},~{rcx},~{r11},~{memory},~{cc}"
            operands = ", ".join(str(v) for v in args + [number])
            # Darwin's ARM assembler treats ';' as a comment, not an
            # instruction separator. LLVM strings encode newlines as \0A.
            asm = asm.replace('; ', '\\0A')
            rty = "{ i64, i64 }" if darwin and arm else "i64"
            result = fb.ins(f'call {rty} asm sideeffect "{asm}", "{constraints}"({operands})', rty)
            if rty != "i64": result = fb.ins(f"extractvalue {result}, 0", "i64")
            return result, None
        if b == "addr": return Val(self.fname(e.target, "seq"), "ptr"), None
        if b == "data":
            a, _ = self.gen(e.args[0], env, False)
            return fb.ins(f"extractvalue {a}, 0", "ptr"), None
        if b in ("view", "handler_call"):
            args = [self.gen(a, env, False)[0] for a in e.args]
            data, ln = args[-2:]
            a = fb.ins(f"insertvalue {ARR} undef, ptr {data.name}, 0", ARR)
            a = fb.ins(f"insertvalue {a}, i64 {ln.name}, 1", ARR)
            a = fb.ins(f"insertvalue {a}, i64 0, 2", ARR)
            if b == "view": return a, Val("0", "i64")
            r = fb.ins(f"call {ARR} {args[0].name}({a})", ARR)
            return r, fb.ins(f"extractvalue {r}, 2", "i64")
        if b == "ptr_add" or (b and (b.startswith(("load_", "store_", "volatile_load_", "volatile_store_")))):
            args = [self.gen(a, env, False)[0] for a in e.args]
            p = fb.ins(f"getelementptr i8, {args[0]}, {args[1]}", "ptr")
            if b == "ptr_add": return p, None
            volatile = "volatile " if b.startswith("volatile_") else ""
            if "load_" in b: return fb.ins(f"load {volatile}{lty(e.ty)}, {p}, align 1", lty(e.ty)), None
            fb.emit(f"store {volatile}{args[2]}, {p}, align 1")
            return Val("0", "i8"), None
        if b=='invoke_finalizer':
            fn,context=[self.gen(arg,env,False)[0] for arg in e.args];fb.emit(f'call void {fn.name}({context})');return Val('0','i8'),None
        if b == "invoke_region":
            fn, ctx, chunk = [self.gen(a, env, False)[0] for a in e.args]
            fb.emit(f"call void {fn.name}({ctx}, {chunk})")
            return Val("0", "i8"), None
        if b == "serve":
            port, _ = self.gen(e.args[0], env, par)
            return fb.ins(f"call i64 @ax_serve(i64 {port.name}, ptr {self.fname(e.handler, 'seq')})", "i64"), None
        if b == "sqrt":
            x, _ = self.gen(e.args[0], env, par)
            return fb.ins(f"call double @llvm.sqrt.f64({x})", "double"), None
        if b == "concat":
            parts = [self.gen(a, env, par) for a in e.args]
            lens = [Val(str(len(a.value)), "i64") if isinstance(a, S.Str) else fb.ins(f"extractvalue {v}, 1", "i64")
                    for (v, _), a in zip(parts, e.args)]
            total = lens[0]
            for l in lens[1:]: total = self.checked_operation('+',total,l,e)
            v0, own0 = parts[0]
            res = fb.alloca(ARR)
            # If we own the first buffer and its capacity class has room, append in place
            # (amortised O(appended bytes), like String::push_str); otherwise allocate and copy all.
            c0 = self.nz(own0)
            grow, fresh, join = fb.label("cat_grow"), fb.label("cat_fresh"), fb.label("cat_join")
            if c0 is None: fb.br(fresh)
            else:
                cap = self.capacity(lens[0])
                fits = fb.ins(f"icmp sle i64 {total.name}, {cap.name}", "i1")
                ok = self.and_(c0, fits)
                fb.cbr(ok, grow, fresh)
                fb.block(grow)
                dst = fb.ins(f"extractvalue {v0}, 0", "ptr")
                off = lens[0]
                for (v, own), l, a in list(zip(parts, lens, e.args))[1:]:
                    src = fb.ins(f"extractvalue {v}, 0", "ptr")
                    d = fb.ins(f"getelementptr inbounds i8, ptr {dst.name}, i64 {off.name}", "ptr")
                    fb.emit(f"call void @llvm.memcpy.p0.p0.i64(ptr {d.name}, ptr {src.name}, i64 {l.name}, i1 false)")
                    off = fb.ins(f"add i64 {off.name}, {l.name}", "i64")
                    self.maybe_free(v, self.nz(own), a.ty)
                grown = fb.ins(f"insertvalue {v0}, i64 {total.name}, 1", ARR)
                grown = fb.ins(f"insertvalue {grown}, i64 1, 2", ARR)
                fb.store(grown, res); fb.br(join)
            fb.block(fresh)
            out = self.new_array(e.ty, total)
            dst = fb.ins(f"extractvalue {out}, 0", "ptr")
            off = Val("0", "i64")
            for (v, own), l, a in zip(parts, lens, e.args):
                src = fb.ins(f"extractvalue {v}, 0", "ptr")
                d = fb.ins(f"getelementptr inbounds i8, ptr {dst.name}, i64 {off.name}", "ptr")
                fb.emit(f"call void @llvm.memcpy.p0.p0.i64(ptr {d.name}, ptr {src.name}, i64 {l.name}, i1 false)")
                off = fb.ins(f"add i64 {off.name}, {l.name}", "i64")
                self.maybe_free(v, self.nz(own), a.ty)
            fb.store(out, res); fb.br(join)
            fb.block(join)
            value=fb.load(ARR,res)
            return value, fb.ins(f'extractvalue {value}, 2','i64')
        if b == 'push':
            t=e.args[0].ty
            a,own=self.gen(e.args[0],env,par)
            value,vown=self.gen(e.args[1],env,par)
            if isinstance(t.elem,S.TArr):value=self.owning_value(value,vown,t.elem)
            length=fb.ins(f'extractvalue {a}, 1','i64')
            total=self.checked_operation('+',length,Val('1','i64'),e)
            old_bytes=fb.ins(f'mul i64 {length.name}, {elem_size(t)}','i64')
            new_bytes=fb.ins(f'mul i64 {total.name}, {elem_size(t)}','i64')
            maximum=(1<<62)//max(1,elem_size(t))
            self.trap_if(fb.ins(f'icmp ugt i64 {total.name}, {maximum}','i1'),e,'array push size overflow')
            result=fb.alloca(ARR);fresh,join=fb.label('push_allocate'),fb.label('push_join')
            flag=self.nz(own)
            if not isinstance(t.elem,S.TRec) and flag is not None:
                grow=fb.label('push_inplace')
                size=elem_size(t)
                if size<=16 and size>0 and size&(size-1)==0:
                    # Adding one power-of-two-sized element crosses the
                    # allocator's rounded capacity exactly at a power-of-two
                    # length (above its minimum 16-byte payload). This is the
                    # same growth policy, without a ctlz/variable shift per push.
                    previous=fb.ins(f'sub i64 {length.name}, 1','i64')
                    bits=fb.ins(f'and i64 {length.name}, {previous.name}','i64')
                    interior=fb.ins(f'icmp ne i64 {bits.name}, 0','i1')
                    small=fb.ins(f'icmp ult i64 {length.name}, {16//size}','i1')
                    fits=fb.ins(f'or i1 {interior.name}, {small.name}','i1')
                else:
                    cap=self.capacity(old_bytes)
                    fits=fb.ins(f'icmp ule i64 {new_bytes.name}, {cap.name}','i1')
                fb.cbr(self.and_(flag,fits),grow,fresh);fb.block(grow)
                grown=fb.ins(f'insertvalue {a}, i64 {total.name}, 1',ARR)
                self.store_elem(grown,length,e.ty,value);fb.store(grown,result);fb.br(join)
            else:fb.br(fresh)
            fb.block(fresh);out=self.new_array(e.ty,total)
            if not managed(t.elem) and not isinstance(t.elem,S.TRec):
                src=fb.ins(f'extractvalue {a}, 0','ptr');dst=fb.ins(f'extractvalue {out}, 0','ptr')
                fb.emit(f'call void @llvm.memcpy.p0.p0.i64(ptr {dst.name}, ptr {src.name}, i64 {old_bytes.name}, i1 false)')
            else:
                # Transfer initialized children when unique; otherwise deep-copy.
                transfer,copy,done=fb.label('push_transfer'),fb.label('push_clone'),fb.label('push_copied')
                fb.cbr(flag or Val('false','i1'),transfer,copy)
                fb.block(transfer)
                self.counted_loop(Val('0','i64'),length,'$push',{},lambda env,i:self.store_elem(out,i,e.ty,self.load_elem(a,i,t)))
                fb.br(done);fb.block(copy)
                self.counted_loop(Val('0','i64'),length,'$push_copy',{},lambda env,i:self.store_elem(out,i,e.ty,self.clone_value(self.load_elem(a,i,t),t.elem)))
                fb.br(done);fb.block(done)
            self.store_elem(out,length,e.ty,value)
            if flag is not None:
                release,done=fb.label('push_release'),fb.label('push_released');fb.cbr(flag,release,done);fb.block(release)
                src=fb.ins(f'extractvalue {a}, 0','ptr');fb.emit(f'call void @ax_free(ptr {src.name}, i64 {old_bytes.name})');fb.br(done);fb.block(done)
            fb.store(out,result);fb.br(join);fb.block(join)
            out=fb.load(ARR,result);return out,fb.ins(f'extractvalue {out}, 2','i64')
        if b == "set":
            t = e.args[0].ty
            a, own = self.gen(e.args[0], env, par)
            i, _ = self.gen(e.args[1], env, par)
            v, vown = self.gen(e.args[2], env, par)
            if isinstance(t.elem, S.TArr): v = self.owning_value(v, vown, t.elem)
            # in place when we own the buffer, else copy-on-write
            res = fb.alloca(ARR)
            c = self.nz(own)
            if c is None or c.name != "true":
                inplace, copy, join = fb.label("set_inplace"), fb.label("set_copy"), fb.label("set_join")
                if c is None: fb.br(copy)
                else: fb.cbr(c, inplace, copy)
                fb.block(copy)
                ln = fb.ins(f"extractvalue {a}, 1", "i64")
                fresh = self.clone_value(a, t)
                if managed(t.elem): self.drop_value(self.load_elem(fresh, i, t), t.elem)
                self.store_elem(fresh, i, t, v)
                fb.store(fresh, res); fb.br(join)
                if c is not None:
                    fb.block(inplace)
                    if managed(t.elem): self.drop_value(self.load_elem(a, i, t), t.elem)
                    self.store_elem(a, i, t, v)
                    fb.store(a, res); fb.br(join)
                fb.block(join)
            else:
                if managed(t.elem): self.drop_value(self.load_elem(a, i, t), t.elem)
                self.store_elem(a, i, t, v)
                fb.store(a, res)
            value=fb.load(ARR,res)
            return value, fb.ins(f'extractvalue {value}, 2','i64')
        signature=getattr(e,'indirect_signature',None)
        if signature is not None:
            from types import SimpleNamespace
            callee=SimpleNamespace(effect=signature.capabilities is not None,exported=False,external=False,variadic=False,params=[S.Param(str(i),t,[]) for i,t in enumerate(signature.params)])
            function=self.fb.load('ptr',env[e.name].ptr).name
        else:
            callee = next(f for f in self.fns if f.name == e.name)
            function=None
        vals = []; temporaries = []
        for argument_index,a in enumerate(e.args):
            if isinstance(a.ty,S.TArr) and argument_index in getattr(e,'borrowed_args',set()):
                view=self.record_argument_view(a,env)
                if view is not None:
                    vals.append(self.with_own(view,Val('0','i64')));continue
            if isinstance(a.ty,S.TRec) and managed(a.ty) and not (callee.effect or callee.exported or callee.external or callee.params[argument_index].owned):
                view = self.record_argument_view(a, env)
                if view is None:
                    view, _ = self.gen(a, env, par)
                    temporaries.append((view,a.ty))
                vals.append(view)
                continue
            v, own = self.gen(a, env, par)
            if own is not None: v = self.with_own(v, own)          # the callee owns what arrives flagged
            vals.append(v)
        mode = "par" if (par and self.mode == "par") else "seq"
        args = ", ".join(str(v) for v in vals)
        rty = return_type(e.ty)
        signature = f" ({', '.join([lty(p.ty) for p in callee.params] + ['...'])})" if callee.variadic else ""
        call = f"call {rty}{signature} {function if function is not None else self.fname(callee, mode)}({args})"
        if e.ty == S.UNIT:
            fb.emit(call)
            for value,ty in temporaries:self.drop_value(value,ty)
            return Val("0", "i8"), None
        r = fb.ins(call, rty)
        for value,ty in temporaries:self.drop_value(value,ty)
        if isinstance(e.ty, S.TArr):
            return r, fb.ins(f"extractvalue {r}, 2", "i64")
        return r, None

    # ------------------------------------------------------------ sum / arr
    def counted_loop(self, lo: Val, hi: Val, var: str, env, body_fn):
        fb = self.fb
        ip = fb.alloca("i64"); fb.store(lo, ip)
        cond, body, end = fb.label("for_cond"), fb.label("for_body"), fb.label("for_end")
        fb.br(cond); fb.block(cond)
        i = fb.load("i64", ip)
        c = fb.ins(f"icmp slt i64 {i.name}, {hi.name}", "i1")
        fb.cbr(c, body, end); fb.block(body)
        env2 = dict(env); env2[var] = Slot(ip, "i64", axty=S.I64)
        body_fn(env2, i)
        i2 = fb.ins(f"add i64 {i.name}, 1", "i64"); fb.store(i2, ip)
        fb.br(cond); fb.block(end)

    def comprehension(self, e, env, par):
        fb = self.fb
        is_sum = isinstance(e, S.Sum)
        # Child allocations after fork are not mapped in the parent. Managed
        # elements therefore stay sequential until a shared aggregate arena exists.
        if not is_sum and managed(e.ty.elem): par = False
        lo, _ = self.gen(e.lo, env, par)
        hi, _ = self.gen(e.hi, env, par)
        if self.checked_integer():self.checked_operation('-',hi,lo,e)
        ety = lty(e.ty)
        if is_sum:
            acc = fb.alloca(ety); fb.store(Val(zero(e.ty), ety), acc)
            out = None
        else:
            ln = fb.ins(f"sub i64 {hi.name}, {lo.name}", "i64")
            out = self.new_array(e.ty, ln)
        vec = is_sum and self.vectorize and e.ty in (S.I64, S.F64) and VZ.eligible(e.body, self.fn_map)
        reads = []
        varr = not is_sum and self.vectorize and e.ty.elem in (S.I64, S.F64) and VZ.eligible(e.body,self.fn_map,array_var=e.var,reads=reads)
        if vec: self.vector_sums.append({"fn": self.fn.name, "node": e.hash[:12], "pos": list(e.pos), "lanes": VZ.K})
        if varr: self.vector_arrays.append({"fn": self.fn.name, "node": e.hash[:12], "pos": list(e.pos), "lanes": VZ.ARRAY_LANES, 'reads':sorted(set(reads))})
        if not par:
            if vec:
                VZ.vector_sum_loop(self, fb, e, lo, hi, env, acc, self.fn_map)
                return fb.load(ety, acc), None
            if varr:
                VZ.vector_array_loop(self,fb,e,lo,hi,env,out,lo,reads)
                return out,fb.ins(f'extractvalue {out}, 2','i64')
            def body(env2, i):
                v, own = self.gen(e.body, env2, par=False)
                if not is_sum and isinstance(e.ty.elem, S.TArr): v = self.owning_value(v, own, e.ty.elem)
                if is_sum:
                    fb.store(self.add_vals(fb.load(ety, acc), v, e.ty), acc)
                else:
                    k = fb.ins(f"sub i64 {i.name}, {lo.name}", "i64")
                    self.store_elem(out, k, e.ty, v)
            # A short little-endian word-to-byte comprehension needs one store,
            # not a loop of variable shifts. Fresh buffers have at least 16
            # bytes of physical capacity; bytes beyond the logical length are
            # inaccessible. Keep the ordinary loop for lengths above eight.
            cast=e.body
            shift=cast.expr if isinstance(cast,S.Cast) and cast.to==S.U8 else None
            word=shift.args[0] if isinstance(shift,S.Call) and shift.name=='shift_right' and len(shift.args)==2 else None
            amount=shift.args[1] if word is not None else None
            packed=(not is_sum and e.ty.elem==S.U8 and isinstance(e.lo,S.Num) and e.lo.value==0
                    and isinstance(word,S.Var) and word.name!=e.var and word.ty==S.I64
                    and isinstance(amount,S.Bin) and amount.op=='*'
                    and isinstance(amount.left,S.Var) and amount.left.name==e.var
                    and isinstance(amount.right,S.Num) and amount.right.value==8
                    and self.triple.startswith(('x86_64','aarch64','arm64')))
            if packed:
                small,ordinary,ready=fb.label('word_bytes'),fb.label('byte_loop'),fb.label('bytes_ready')
                nonempty=fb.ins(f'icmp sgt i64 {hi.name}, 0','i1')
                short=fb.ins(f'icmp sle i64 {hi.name}, 8','i1')
                fb.cbr(self.and_(nonempty,short),small,ordinary)
                fb.block(small)
                value,_=self.gen(word,env,par=False)
                data=fb.ins(f'extractvalue {out}, 0','ptr')
                fb.emit(f'store i64 {value.name}, ptr {data.name}, align 1')
                fb.br(ready);fb.block(ordinary)
                self.counted_loop(lo,hi,e.var,env,body)
                fb.br(ready);fb.block(ready)
                return out,fb.ins(f'extractvalue {out}, 2','i64')
            self.counted_loop(lo, hi, e.var, env, body)
            return (fb.load(ety, acc), None) if is_sum else (out, fb.ins(f'extractvalue {out}, 2','i64'))

        # ---- parallel: outline the body into @<fn>__regionK(ptr ctx, i64 chunk)
        from .kernel_workers import allocation_free
        shared = 'linux' in self.triple and allocation_free(e.body,self.fn_map)
        dispatcher = 'ax_par_for_shared' if shared else 'ax_par_for'
        self.par_regions.append({"fn": self.fn.name, "node": e.hash[:12], "pos": list(e.pos), "kind": "sum" if is_sum else "arr", 'dispatcher':dispatcher})
        d = fb.ins(f"sub i64 {hi.name}, {lo.name}", "i64")
        pos = fb.ins(f"icmp sgt i64 {hi.name}, {lo.name}", "i1")
        n = fb.ins(f"select i1 {pos.name}, i64 {d.name}, i64 0", "i64")
        cost=VZ.work_cost(e.body) if shared else None
        self.par_regions[-1]['work_cost']=cost
        nchunks = fb.ins(f"call i64 @ax_chunks_work(i64 {n.name}, i64 {cost})" if cost is not None else f"call i64 @ax_chunks(i64 {n.name})", "i64")
        esz = 8 if not isinstance(e.ty, S.TRec) else sum(scalar_size(ft) for _, ft in e.ty.fields)
        if is_sum:
            pby = fb.ins(f"mul i64 {nchunks.name}, {max(esz, 8)}", "i64")
            target = fb.ins(f"call ptr @ax_alloc(i64 {pby.name})", "ptr")
        else:
            target = out
        captured = [(name, slot) for name, slot in env.items()]
        cty = "{ i64, i64, i64, " + ("ptr" if is_sum else ARR) + "".join(f", {s.ty}" for _, s in captured) + " }"
        ctx = fb.alloca(cty)
        fields = [lo, n, nchunks, target] + [fb.load(s.ty, s.ptr) for _, s in captured]
        for k, v in enumerate(fields):
            p = fb.ins(f"getelementptr inbounds {cty}, ptr {ctx}, i32 0, i32 {k}", "ptr")
            fb.store(v, p.name)
        self.region_k += 1
        rname = f"@ax_{self.fn.name}__region{self.region_k}"
        fb.emit(f"call void @{dispatcher}(i64 {nchunks.name}, ptr {rname}, ptr {ctx})")
        saved = self.fb
        rb = FuncBuilder(f"define internal void {rname}(ptr %ctx, i64 %chunk)")
        self.fb = rb
        def fld(k, ty):
            p = rb.ins(f"getelementptr inbounds {cty}, ptr %ctx, i32 0, i32 {k}", "ptr")
            return rb.load(ty, p.name)
        rlo, rn, rch = fld(0, "i64"), fld(1, "i64"), fld(2, "i64")
        rtgt = fld(3, "ptr" if is_sum else ARR)
        renv: Dict[str, Slot] = {}
        for k, (name, s) in enumerate(captured):
            v = fld(4 + k, s.ty); ptr = rb.alloca(s.ty); rb.store(v, ptr)
            renv[name] = Slot(ptr, s.ty, axty=s.axty)                 # arrays are borrowed inside regions
            if s.ty == ARR and isinstance(s.axty, S.TArr): self.assume_array(v, s.axty)
        a = rb.ins(f"mul i64 {rn.name}, %chunk", "i64")
        clo = rb.ins(f"sdiv i64 {a.name}, {rch.name}", "i64"); clo = rb.ins(f"add i64 {clo.name}, {rlo.name}", "i64")
        c1 = rb.ins("add i64 %chunk, 1", "i64"); b = rb.ins(f"mul i64 {rn.name}, {c1.name}", "i64")
        chi = rb.ins(f"sdiv i64 {b.name}, {rch.name}", "i64"); chi = rb.ins(f"add i64 {chi.name}, {rlo.name}", "i64")
        racc = rb.alloca(ety) if is_sum else None
        if is_sum: rb.store(Val(zero(e.ty), ety), racc)
        def rbody(env2, i):
            v, _ = self.gen(e.body, env2, par=False)
            if is_sum:
                rb.store(self.add_vals(rb.load(ety, racc), v, e.ty), racc)
            else:
                k = rb.ins(f"sub i64 {i.name}, {rlo.name}", "i64")
                self.store_elem(rtgt, k, e.ty, v)
        if vec: VZ.vector_sum_loop(self, rb, e, clo, chi, renv, racc, self.fn_map)
        elif varr: VZ.vector_array_loop(self,rb,e,clo,chi,renv,rtgt,rlo,reads)
        else: self.counted_loop(clo, chi, e.var, renv, rbody)
        if is_sum:
            p = rb.ins(f"getelementptr inbounds {ety}, ptr {rtgt.name}, i64 %chunk", "ptr")
            rb.store(rb.load(ety, racc), p.name)
        rb.emit("ret void")
        self.funcs.append(rb.render())
        self.fb = fb = saved
        if is_sum:
            def red(env2, c):
                p = fb.ins(f"getelementptr inbounds {ety}, ptr {target.name}, i64 {c.name}", "ptr")
                fb.store(self.add_vals(fb.load(ety, acc), fb.load(ety, p.name), e.ty), acc)
            self.counted_loop(Val("0", "i64"), nchunks, "%chunk", env, red)
            fb.emit(f"call void @ax_free(ptr {target.name}, i64 {pby.name})")
            return fb.load(ety, acc), None
        return out, fb.ins(f'extractvalue {out}, 2','i64')

    # ------------------------------------------------------------ loop
    def loop(self, e: S.Loop, env, par):
        fb = self.fb
        slots: List[Slot] = []
        env2 = dict(env)
        for name, init, t in zip(e.names, e.inits, e.state_tys):
            v, own = self.gen(init, env, par)
            ptr = fb.alloca(v.ty); fb.store(v, ptr)
            s = Slot(ptr, v.ty, axty=t)
            if v.ty == ARR:
                s.own_ptr = fb.alloca("i64"); fb.store(own, s.own_ptr)
                s.consume = consume_plan(list(e.nexts), name) | consume_plan([e.yld], name)
                s.field_consume = field_consume_plan(list(e.nexts)+[e.yld],name,t)
                self.assume_array(v, t)
            elif managed(t):
                s.own_ptr = fb.alloca('i64'); fb.store(Val('1','i64'),s.own_ptr)
                s.consume = consume_plan(list(e.nexts), name) | consume_plan([e.yld], name)
                s.field_consume = field_consume_plan(list(e.nexts)+[e.yld],name,t)
            slots.append(s); env2[name] = s
        cond, body, exit_ = fb.label("loop_cond"), fb.label("loop_body"), fb.label("loop_exit")
        fb.br(cond); fb.block(cond)
        c, _ = self.gen(e.cond, env2, par)
        fb.cbr(c, body, exit_); fb.block(body)
        nexts = [self.gen(nx, env2, par) for nx in e.nexts]
        # Old buffers may be freed only when every array successor is fresh or moved (own != 0),
        # since a borrowed successor (own == 0) may alias an old buffer.
        all_fresh: Optional[Val] = Val("true", "i1")
        for (v, own), t in zip(nexts, e.state_tys):
            if isinstance(t, S.TArr): all_fresh = self.and_(all_fresh, self.nz(own))
        for s, (v, own), t in zip(slots, nexts, e.state_tys):
            if isinstance(t, S.TRec) and managed(t): self.drop_record_slot(s)
            if isinstance(t, S.TArr) and s.own_ptr and all_fresh is not None:
                self.maybe_free(fb.load(ARR, s.ptr), self.and_(self.nz(fb.load("i64", s.own_ptr)), all_fresh), t)
        for s, (v, own), t in zip(slots, nexts, e.state_tys):
            fb.store(v, s.ptr)
            if s.own_ptr:
                fb.store(own if isinstance(t,S.TArr) else Val('1','i64'), s.own_ptr)
                if isinstance(t,S.TArr): self.assume_array(v, t)
        fb.br(cond); fb.block(exit_)
        yv, yown = self.gen(e.yld, env2, par)
        self.free_scope(slots, yown if isinstance(e.yld.ty, S.TArr) else None)
        return yv, yown
