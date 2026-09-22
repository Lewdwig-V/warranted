import Init

namespace Warranted

-- The assumed offset is a model parameter, not a claim about the source contract.
def normalizeTime (localSeconds assumedOffsetSeconds : Int) : Int :=
  localSeconds - assumedOffsetSeconds

def TimestampTarget : Prop :=
  ∀ (localSeconds assumedOffsetSeconds : Int),
    normalizeTime localSeconds assumedOffsetSeconds + assumedOffsetSeconds = localSeconds

theorem timestamp_roundtrip : TimestampTarget := by sorry

end Warranted
