"""Minimal Linux clone stack switch, with Axon-owned scheduling and stacks.

The raw syscall returns on a different stack in the child, so this boundary
cannot be expressed as an ordinary LLVM call followed by Axon instructions.
ABI: work, context, first chunk, end chunk, 16-byte-aligned stack top.
CLONE_VM | SIGCHLD shares mappings but retains waitable process identities.
https://man7.org/linux/man-pages/man2/clone.2.html
"""

def assembly(triple):
    if 'linux' not in triple: return []
    if triple.startswith(('aarch64','arm64')):
        code = '''
.text
.p2align 4
.global ax_spawn_region
.hidden ax_spawn_region
.type ax_spawn_region,%function
ax_spawn_region:
sub x4, x4, #48
stp x0, x1, [x4]
stp x2, x3, [x4, #16]
mov x1, x4
mov x0, #273
mov x2, #0
mov x3, #0
mov x4, #0
mov x8, #220
svc #0
cbz x0, .Lax_worker_child
ret
.Lax_worker_child:
ldp x19, x20, [sp]
ldp x21, x22, [sp, #16]
.Lax_worker_loop:
cmp x21, x22
b.ge .Lax_worker_exit
mov x0, x20
mov x1, x21
blr x19
add x21, x21, #1
b .Lax_worker_loop
.Lax_worker_exit:
mov x0, #0
mov x8, #93
svc #0
brk #0
.size ax_spawn_region, .-ax_spawn_region
'''
    else:
        code = '''
.text
.p2align 4
.global ax_spawn_region
.hidden ax_spawn_region
.type ax_spawn_region,@function
ax_spawn_region:
subq $48, %r8
movq %rdi, 0(%r8)
movq %rsi, 8(%r8)
movq %rdx, 16(%r8)
movq %rcx, 24(%r8)
movq %r8, %rsi
movq $273, %rdi
xorl %edx, %edx
xorl %r10d, %r10d
xorl %r8d, %r8d
movl $56, %eax
syscall
testq %rax, %rax
jz .Lax_worker_child
retq
.Lax_worker_child:
popq %r12
popq %r13
popq %r14
popq %r15
.Lax_worker_loop:
cmpq %r15, %r14
jge .Lax_worker_exit
movq %r13, %rdi
movq %r14, %rsi
callq *%r12
incq %r14
jmp .Lax_worker_loop
.Lax_worker_exit:
xorl %edi, %edi
movl $60, %eax
syscall
ud2
.size ax_spawn_region, .-ax_spawn_region
'''
    return ['module asm "'+code.strip().replace('\n','\\0A')+'"',
            'declare hidden i64 @ax_spawn_region(ptr, ptr, i64, i64, ptr)']


def allocation_free(node, fns, seen=frozenset()):
    """Certify a sequential region body cannot allocate or mutate runtime state.

    Array parameters are borrowed; managed record values/projections, owned
    parameters, array construction/mutation and unknown calls are rejected.
    Scalar reductions and finite unmanaged record operations use stack storage.
    This is a dispatcher safety predicate, not a profitability estimate.
    """
    from . import syntax as S
    def managed(ty):
        return isinstance(ty,S.TArr) or isinstance(ty,S.TRec) and any(managed(t) for _,t in ty.fields)
    if isinstance(node,S.ArrC):return False
    if isinstance(node,(S.RecLit,S.With,S.Index,S.Field)) and managed(node.ty):return False
    if isinstance(node,S.Var) and isinstance(node.ty,S.TRec) and managed(node.ty):return False
    if isinstance(node,S.Loop) and any(managed(t) for t in node.state_tys):return False
    if isinstance(node,S.Sum) and managed(node.ty):return False
    if isinstance(node,S.Call):
        builtin=getattr(node,'builtin',None)
        if builtin:
            if builtin!='sqrt':return False
        else:
            callee=fns.get(node.name)
            if callee is None or callee.effect or callee.external or callee.exported or callee.name in seen or managed(callee.ret) or any(p.owned or isinstance(p.ty,S.TRec) and managed(p.ty) for p in callee.params):return False
            if not allocation_free(callee.body,fns,seen|{callee.name}):return False
    return all(allocation_free(child,fns,seen) for child in S.children(node))
