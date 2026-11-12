import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "gen_nest_handlers.py"
_SPEC = importlib.util.spec_from_file_location("gen_nest_handlers", SCRIPT)
assert _SPEC is not None
assert _SPEC.loader is not None
generator = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(generator)

SOURCE = """
@Controller({ path: 'auth', version: '1' })
export class AuthController {
  @ApiOperation({ operationId: 'loginWithEmail', summary: '邮箱登录' })
  @ApiOkResponse({
    type: LoginResponseDto,
  })
  @HttpCode(HttpStatus.OK)
  public login(@Body() dto: AuthEmailLoginDto): Promise<LoginResponseDto> {
    return this.service.login(dto);
  }

  @ApiOperation({
    operationId: 'refreshToken',
  })
  async refresh(@Request() request): Promise<void> {}
}

export class MetricsController {
  @ApiOperation({ operationId: 'probe' })
  override index(@Res() response: Response): Promise<string> {}
}
"""


def test_maps_operation_ids_to_controller_handlers() -> None:
    assert generator.handlers_in(SOURCE) == {
        "loginWithEmail": "AuthController-login",
        "refreshToken": "AuthController-refresh",
        "probe": "MetricsController-index",
    }
