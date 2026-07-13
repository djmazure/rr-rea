# REA trigger-pipeline refactor

Contract-first design rationale for RTL-P3.1165. This note defines the
requirements the RTL refactor must meet; it does not prescribe an RTL
implementation.

## Problem in the current RTL

`G_TRIGGER_PIPE_STAGES` is not a comparator-decomposition control today. The
legacy comparator is concurrent (`legacy_hit`,
[rr_rea_capture_fsm.vhd:320-339](../rtl/rr_rea_capture_fsm.vhd#L320-L339));
the sequencer comparators are concurrent generated assignments
([rr_rea_capture_fsm.vhd:300-314](../rtl/rr_rea_capture_fsm.vhd#L300-L314));
and the per-condition comparators and their AND reduction are concurrent
([rr_rea_capture_fsm.vhd:341-380](../rtl/rr_rea_capture_fsm.vhd#L341-L380)).
The priority selection and external-pin fold are also concurrent
([rr_rea_capture_fsm.vhd:382-400](../rtl/rr_rea_capture_fsm.vhd#L382-L400)),
and `seq_final_fire` is a concurrent final-stage decision
([rr_rea_capture_fsm.vhd:455-468](../rtl/rr_rea_capture_fsm.vhd#L455-L468)).
Thus all three local paths form an unbroken combinational comparator tree from
the already retimed probe to `effective_internal`.

The only register that receives comparator logic is pipeline element 1:
`fire_pipe_r(1)` captures `effective_internal` while `ptr_pipe_r(1)` captures
`wr_ptr_r` ([rr_rea_capture_fsm.vhd:402-437](../rtl/rr_rea_capture_fsm.vhd#L402-L437)).
Elements 2 through `G_TRIGGER_PIPE_STAGES` are only fire/pointer shifts
([rr_rea_capture_fsm.vhd:438-447](../rtl/rr_rea_capture_fsm.vhd#L438-L447)).
The pointer pairing is necessary for the trigger-sample invariant, but added
elements cannot shorten the comparator timing path.

The input generic has the same inert shape. `rr_rea_top` exposes
`G_INPUT_PIPE_STAGES` and `G_TRIGGER_PIPE_STAGES`
([rr_rea_top.vhd:28-38](../rtl/rr_rea_top.vhd#L28-L38)); its input pipeline
captures `probe_in` once and then uses a pure shift register
([rr_rea_top.vhd:381-407](../rtl/rr_rea_top.vhd#L381-L407)). It is retiming
latency, not comparator logic decomposition.

## Target architecture

The implementation SHALL define these internal constants:

```
C_SLICE_W        = 8      -- fixed internal constant, NOT a generic
C_WIDTH_STAGES   = ceil(G_SAMPLE_W / C_SLICE_W)
C_REDUCE_STAGES  = ceil_log2(G_TRIG_CONDS)
N                = C_WIDTH_STAGES + C_REDUCE_STAGES
```

`C_SLICE_W` is a fixed internal constant (8), never a generic: the slice count,
carry-chain depth, and reduction fan-in are all generated against it, so
exposing it would only add dead `if...generate` branches for a decision made at
design time. `ceil_log2(1)` is zero, so `N >= 1`. A configuration with
`G_SAMPLE_W <= C_SLICE_W`, one condition and the sequencer disabled has `N=1`,
matching today's zero-to-one effective-stage behaviour; wider probes and larger
comparator-array fan-in add stages. Because the pointer tag travels
with the matching sample (REA-REQ-322), that extra report latency is invisible
to the capture result — so a shallow slice is chosen unconditionally rather than
derived from a timing target: for a capture analyzer the stages are effectively
free (a few flip-flops and report-cycle latency nobody observes), and a shallow
slice keeps the per-stage comparator path short. Width creates the slice stages;
comparator-array fan-in creates the registered binary-reduction stages.
Sequencer stages are excluded because they are consumed in order by state, not
reduced simultaneously. Added latency remains a consequence of real structure.

Every local-trigger token carries the sample's write-pointer tag. The compare
portion processes masked fields in slices of at most `C_SLICE_W` (8) bits,
registering the partial comparison after each slice. The reduction portion
registers each binary AND level across conditions. Later slice operands travel
through registered transport alongside the token, and non-array modes align
across the condition-reduction stages; neither is an arbitrary user-selected
output delay. Both user-visible depth generics disappear. The existing scalar
`ext_trigger_in` and `trigger_in` ports remain unchanged and do not affect N.

## Requirements introduced

| Requirement | Contract |
| --- | --- |
| REA-REQ-320 | Derive `N` from width and comparator-array fan-in. |
| REA-REQ-321 | Register real `C_SLICE_W`-wide (8-bit) compare and binary-reduce work; prove the timing partition structurally. |
| REA-REQ-322 | Carry the matching sample's pointer tag through every derived stage. |
| REA-REQ-323 | Remove both user-facing depth generics and preserve narrow legacy behaviour. |
| REA-REQ-324 | Remove all pipeline-depth generics; keep existing external-trigger ports latency-independent. |
| REA-REQ-325 | More derived depth changes report time only, never the selected sample. |
| REA-REQ-326 | Uniform per-slice `{eq, gt, lt}` lexicographic monoid token (+ OR-reduced RISE/FALL edge bits) so every op decomposes by the same registered tree. |
| REA-REQ-327 | Pipeline every comparator path (legacy/array/per-sequencer-stage) uniformly; run the sequencer FSM on the order-preserved, delayed, tagged match stream. |

### Locked slice-token algorithm (REA-REQ-326)

Each `C_SLICE_W`-wide masked field-slice emits a 3-state token `{eq, gt, lt}`.
Two nodes combine associatively — a monoid, so each higher slice combines with
the registered lower-slice accumulator in one `C_WIDTH_STAGES` stage:

```
eq = eq_hi and eq_lo
gt = gt_hi or (eq_hi and gt_lo)
lt = lt_hi or (eq_hi and lt_lo)
```

At the root: `EQ = eq`, `NE = not eq`, `GT = gt`, `LT = lt` (masked-unsigned,
matching REA-REQ-610/611/620/621/630). `RISE`/`FALL` are per-slice edge bits
(`cur and not prev` / `prev and not cur`, masked, from a registered previous
sample) reduced by `OR`. This resolves the "how does every op slice" question:
one uniform token, one tree, no equality-only special case, no ad-hoc carry.

### Sequencer under the pipeline (REA-REQ-327)

Every local comparator path — legacy, the `G_TRIG_CONDS` array, and each
sequencer stage's own comparator — goes through the SAME `N`-stage pipeline. A
pipeline is order-preserving, so matches emerge in sample order, delayed by a
uniform `N`, each carrying its `wr_ptr` tag. The sequencer FSM advances on this
delayed stream, so a later sample can never advance/fire a stage before an
earlier sample's result is consumed. Uniform latency, no reordering →
REA-REQ-325 holds by construction.

REA-REQ-301 and REA-REQ-302 remain in `requirements.yml` as annotated legacy
contracts. They document the pre-RTL-P3.1165 user-generic behaviour and are
superseded once the derived implementation lands.

## Verification strategy

| Requirement | Verification |
| --- | --- |
| REA-REQ-320 | Cocotb parameter matrix in `sim/cocotb/tests/rea/`: widths on both sides of 32-bit boundaries plus varying condition and sequencer counts; observe the specified `N`-cycle decision latency and tag propagation. |
| REA-REQ-321 | Post-synthesis structural-netlist and STA path-length review. Simulation cannot prove that a register-to-register cone contains at most one field comparison and one binary reduction. The review rejects arbitrary output-only stages while allowing required operand/alignment transport. |
| REA-REQ-322 | Cocotb counter-pattern/readback test, extending the existing REA-REQ-102/103 coverage across every derived-depth case. |
| REA-REQ-323 | Cocotb narrow configuration regression against REA-REQ-100..106 and REA-REQ-300..302, plus elaboration/API checks that reject the removed generics. |
| REA-REQ-324 | API-removal checks plus the existing `trigger_in` and ext-pin tests covering REA-REQ-400/401 and REA-REQ-410..413. |
| REA-REQ-325 | Cocotb equivalence matrix: feed identical timestamped samples to shallow and deeper derived configurations; compare accepted sample values, pointer tags, and DPRAM locations while allowing only report-cycle latency to differ. |

No simulation, synthesis, or implementation run is part of this requirements
task.

## Open RTL design questions

1. RESOLVED (REA-REQ-326): the per-slice token is the `{eq, gt, lt}`
   lexicographic monoid above (+ OR-reduced RISE/FALL edge bits) — one uniform
   token covers EQ/NE/LT/GT/RISE/FALL, no ordered-compare carry hack.
2. RESOLVED (REA-REQ-326 + REA-REQ-322): each stage carries the registered
   partial `{eq, gt, lt}` (+ edge) token and `wr_ptr` tag; registered operand
   transport supplies the later 8-bit slices without adding decision logic.
3. RESOLVED (REA-REQ-327): every comparator path (incl. each sequencer stage's)
   is pipelined uniformly, so the sequencer FSM runs on an order-preserved,
   uniformly-delayed, tagged match stream — a later sample cannot advance a
   stage before an earlier sample's result is consumed.
4. DEFERRED: any multi-input external-trigger port shape, arbitration, and CDC
   treatment needs its own API contract. This refactor preserves the scalar
   REA-REQ-400/401 and REA-REQ-410..413 interfaces.
5. Establish the canonical synthesis-netlist/STA check and report format that
   demonstrates REA-REQ-321 on each supported vendor flow.
