From VRGoal Require Import Expected.

Lemma discharge : Expected.obligation.
Proof.
  unfold Expected.obligation.
  intros P evidence.
  exact evidence.
Qed.
