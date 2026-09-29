"""Admission to model work follows the shared journey, not a browser button."""
from .scheduler import SupersededError


class DetectionPausedError(SupersededError):
    pass


class DetectionGate:
    def __init__(self, policy=None):
        self.policy = policy or (lambda: {'enabled': True, 'generation': 0})

    def capture(self, source_role='unassigned'):
        """Freeze admission at frame receipt, before upload/decoder/GPU delays."""
        policy = self.policy()
        inside = source_role == 'inside'
        return {
            'enabled': bool(policy.get('inside_enabled', policy['enabled']) if inside else policy['enabled']),
            'generation': policy.get('inside_generation', policy['generation']) if inside else policy['generation'],
            'posture_token': (bool(policy.get('posture_enabled', True)), policy.get('posture_generation', 0)),
        }

    def enabled(self, source_role='unassigned'):
        return self.capture(source_role)['enabled']

    def check(self, generation=None, source_role='unassigned'):
        policy = self.capture(source_role)
        if not policy['enabled']:
            raise DetectionPausedError('Outside detection is paused. It resumes when the bus is stationary and the doors are fully open.')
        if generation is not None and generation != policy['generation']:
            raise DetectionPausedError('The doors or journey changed while this frame was processing. Waiting for a fresh open-door frame.')
        return policy['generation']

    def posture_token(self):
        return self.capture('inside')['posture_token']

    def posture_allowed(self, captured):
        current = self.posture_token()
        return bool(captured and captured[0] and current[0] and captured == current)
